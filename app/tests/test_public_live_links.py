"""Public live grants use isolated camera tables and mocked camera processes."""

from __future__ import annotations

import logging
import time

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.core.logging_config import UTCFormatter
from app.middleware import auth_and_setup
from app.models.camera import Camera
from app.models.config import Configuration
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist
from app.routes import auth, map_live, wa_webhook
from app.tests.test_map_live import live_client  # noqa: F401 - reusable isolated fixture
from app.utils import live_stream, public_live_access, public_live_token
from app.utils.public_live_token import create_live_grant, verify_live_grant
from app.utils.wa_bot_commands import live_response_template


@pytest.fixture
def public_client(request, monkeypatch):
    isolated = request.getfixturevalue("live_client")
    db = isolated.db
    WhatsappWhitelist.__table__.create(db.get_bind())
    sender = WhatsappWhitelist(id=501, phone_number="628123456789", role="admin", group_id=101)
    db.add(sender)
    db.commit()
    owner = SimpleNamespace(id=7, role="admin", group_id=101)

    class PolicySession:
        def query(self, model):
            if model is User:
                query = Mock()
                query.filter.return_value.first.return_value = owner
                return query
            return db.query(model)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    policy_db = PolicySession()
    isolated.client.app.dependency_overrides[map_live.get_db] = lambda: policy_db
    isolated.client.app.dependency_overrides.pop(auth.get_current_user)
    monkeypatch.setattr(public_live_access, "SessionLocal", lambda: policy_db)
    token, expires = create_live_grant("shared", 7, sender.id, 30)
    return SimpleNamespace(
        **vars(isolated),
        owner=owner,
        sender=sender,
        token=token,
        expires=expires,
        url=f"/public/cameras/shared/live?token={token}",
    )


def test_public_view_needs_no_login_but_requires_a_valid_grant(public_client):
    response = public_client.client.get(public_client.url)
    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text
    assert 0 < public_client.factory.call_args.kwargs["seconds"] <= 30
    assert public_client.reserve.call_args.args == ("7",)
    assert public_client.audit.call_args.kwargs["action"] == "view_public_camera_live"
    assert public_client.token not in str(public_client.audit.call_args)


@pytest.mark.parametrize("kind", ["missing", "altered", "other-camera", "expired"])
def test_bad_grants_never_start_camera_io(public_client, monkeypatch, kind):
    token = public_client.token
    path = "/public/cameras/shared/live"
    if kind == "missing":
        token = ""
    elif kind == "altered":
        token = "x" + token
    elif kind == "other-camera":
        path = "/public/cameras/other/live"
    else:
        monkeypatch.setattr(
            public_live_token, "time", SimpleNamespace(time=lambda: public_client.expires)
        )
    assert public_client.client.get(path, params={"token": token}).status_code == 403
    public_client.source.assert_not_called()
    public_client.factory.assert_not_called()
    public_client.reserve.assert_not_called()


@pytest.mark.parametrize(
    "revocation",
    [
        "owner-role",
        "owner-group",
        "sender-role",
        "sender-group",
        "sender-deleted",
        "camera-status",
        "disabled",
    ],
)
def test_access_changes_revoke_links_and_active_policy(public_client, revocation):
    assert public_live_access.public_live_policy_allows("shared", public_client.token)
    if revocation == "owner-role":
        public_client.owner.role = "viewer"
    elif revocation == "owner-group":
        public_client.owner.group_id = 999
    elif revocation == "sender-role":
        public_client.sender.role = "user"
    elif revocation == "sender-group":
        public_client.sender.group_id = 999
    elif revocation == "sender-deleted":
        public_client.db.delete(public_client.sender)
    elif revocation == "camera-status":
        public_client.db.get(Camera, "shared").status = "Restricted"
    else:
        public_client.db.get(Configuration, "map_live_enabled").value = "0"
    public_client.db.commit()
    assert not public_live_access.public_live_policy_allows("shared", public_client.token)
    assert public_client.client.get(public_client.url).status_code == 403
    public_client.source.assert_not_called()


def test_token_domain_and_ttl_and_expiry(monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(public_live_token, "time", SimpleNamespace(time=lambda: now))
    token, expires = create_live_grant("cam", 1, 2, 300)
    assert expires == now + 300
    assert verify_live_grant("cam", token)["issuer"] == 1
    monkeypatch.setattr(public_live_token, "time", SimpleNamespace(time=lambda: expires))
    with pytest.raises(HTTPException):
        verify_live_grant("cam", token)
    for seconds in (0, 29, 901):
        with pytest.raises(ValueError, match="30 to 900"):
            create_live_grant("cam", 1, 2, seconds)


def test_grants_are_redacted_in_logs_and_message_records():
    token, _expires = create_live_grant("cam", 1, 2, 300)
    url = f"https://bsnap.example/public/cameras/cam/live?token={token}"
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "GET %s", (url,), None)
    assert token not in UTCFormatter().format(record)
    db = Mock()
    wa_webhook._record_wa_message(db, "phone", "outbound", "accepted", url)
    assert token not in db.add.call_args.args[0].message
    assert "[REDACTED]" in db.add.call_args.args[0].message


def test_link_expiring_during_first_frame_is_not_served(public_client, monkeypatch):
    clock = {"now": time.time()}
    monkeypatch.setattr(map_live, "time", SimpleNamespace(time=lambda: clock["now"]))

    def expired_frame():
        clock["now"] = public_client.expires
        return b"frame"

    public_client.preview.first_frame.side_effect = expired_frame
    assert public_client.client.get(public_client.url).status_code == 403
    public_client.preview.close.assert_called_once()
    public_client.audit.assert_not_called()


def test_ffmpeg_watchdog_and_input_duration_respect_remaining_grant(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    timer = Mock()
    monkeypatch.setattr(live_stream.subprocess, "Popen", popen)
    monkeypatch.setattr(live_stream.threading, "Timer", timer)
    release = Mock()
    preview = live_stream.LivePreview("rtsp://camera.example/live", release, seconds=1.5)
    command = popen.call_args.args[0]
    assert command[command.index("-t") + 1] == "1.5"
    assert timer.call_args.args[0] == 1.5
    timer.call_args.args[1]()
    assert preview.closed
    release.assert_called_once()


def test_process_startup_does_not_extend_absolute_expiry(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(live_stream.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(live_stream, "time", lambda: 105)
    timer = Mock()
    monkeypatch.setattr(live_stream.threading, "Timer", timer)
    preview = live_stream.LivePreview(
        "rtsp://camera.example/live", Mock(), seconds=30, expires_at=110
    )
    assert timer.call_args.args[0] == 5
    preview.close()


def test_generated_legacy_login_caption_becomes_expiry_notice():
    old = "{{steps.result.body.camera}}\n{{steps.result.body.url}}\nB-Snap admin login required."
    assert "expires_at|datetime" in live_response_template(old)
    assert (
        live_response_template("Custom link: {{steps.result.body.url}}")
        == "Custom link: {{steps.result.body.url}}"
    )


@pytest.mark.asyncio
async def test_only_exact_get_public_live_route_bypasses_session_auth(monkeypatch):
    middleware = auth_and_setup.AuthAndSetupMiddleware(FastAPI())
    call_next = AsyncMock(return_value=Response())
    database = Mock()
    monkeypatch.setattr(auth_and_setup, "SessionLocal", database)
    middleware._do_dispatch = AsyncMock(return_value=Response())
    request = Request(
        {"type": "http", "method": "GET", "path": "/public/cameras/cam/live", "headers": []}
    )
    await middleware.dispatch(request, call_next)
    call_next.assert_awaited_once()
    database.assert_not_called()
    for method, path in [
        ("POST", "/public/cameras/cam/live"),
        ("GET", "/public/cameras/cam/live/extra"),
        ("GET", "/maps/cameras/cam/live"),
    ]:
        request = Request(
            {"type": "http", "method": method, "path": path, "headers": [], "session": {}}
        )
        await middleware.dispatch(request, call_next)
    assert middleware._do_dispatch.await_count == 3
