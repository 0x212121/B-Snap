"""Audit regressions with temporary footage, mocked transports, and isolated tables."""

from __future__ import annotations

import asyncio
import json

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pyotp
import pytest

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import main
from app.api import observability_log, whatsapp_routes
from app.core.static_assets import PublicStaticFiles
from app.db.database import Base, get_db
from app.middleware.auth_and_setup import AuthAndSetupMiddleware
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.video import Video
from app.routes import (
    auth,
    cameras,
    config,
    group_recipients,
    ping,
    snap_gallery,
    snapshots,
    toast_demo,
    videos,
    wa_webhook,
    whitelist,
)
from app.utils.auth_token import is_valid_web_token
from app.utils.snapshot_service import SnapshotService
from app.ws import record_notifications, routes as websocket_routes


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the global JSONB schema fixture; never start application services."""


@pytest.mark.parametrize("folder", ["snapshots", "videos"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("alias", ["{folder}", "/{folder}", "%2F{folder}", "{folder_upper}"])
def test_static_footage_aliases_are_forbidden(tmp_path, folder, method, alias):
    directory = tmp_path / folder
    directory.mkdir()
    (directory / "private.jpg").write_bytes(b"evidence")
    (tmp_path / "public.css").write_text("body {}", encoding="utf-8")
    app = FastAPI()
    app.mount("/static", PublicStaticFiles(directory=tmp_path))
    path = alias.format(folder=folder, folder_upper=folder.upper())
    with TestClient(app) as client:
        assert client.request(method, f"/static/{path}/private.jpg").status_code == 403
        assert client.get("/static/public.css").status_code == 200


def test_static_symlink_cannot_alias_private_footage(tmp_path):
    private = tmp_path / "snapshots"
    private.mkdir()
    (private / "private.jpg").write_bytes(b"evidence")
    try:
        (tmp_path / "alias").symlink_to(private, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable for this Windows account")
    app = FastAPI()
    app.mount("/static", PublicStaticFiles(directory=tmp_path))
    with TestClient(app) as client:
        assert client.get("/static/alias/private.jpg").status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_enabled_mfa_never_exposes_or_reenrolls_secret(monkeypatch, method):
    user = SimpleNamespace(id=7, is_2fa_enabled=True, otp_secret=pyotp.random_base32())
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = user
    db.query.return_value.options.return_value.filter.return_value.first.return_value = user
    renderer = Mock()
    authenticated = Mock()
    monkeypatch.setattr(auth.templates, "TemplateResponse", renderer)
    monkeypatch.setattr(auth, "_create_authenticated_session", authenticated)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="mfa-audit")
    app.include_router(auth.router)
    app.dependency_overrides[get_db] = lambda: db
    token = auth.create_2fa_token(user.id)
    with TestClient(app) as client:
        response = client.request(
            method,
            "/mfa/setup",
            params={"token": token} if method == "GET" else None,
            data=(
                {"token_mfa": token, "otp": pyotp.TOTP(user.otp_secret).now()}
                if method == "POST"
                else None
            ),
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    renderer.assert_not_called()
    authenticated.assert_not_called()
    db.commit.assert_not_called()


def test_initial_mfa_enrollment_still_works(monkeypatch):
    user = SimpleNamespace(
        id=7, username="enrollment", is_2fa_enabled=False, otp_secret=pyotp.random_base32()
    )
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = user
    db.query.return_value.options.return_value.filter.return_value.first.return_value = user
    rendered = Mock(return_value=JSONResponse({"enrollment": True}))
    authenticated = Mock(return_value=JSONResponse({"authenticated": True}))
    monkeypatch.setattr(auth.templates, "TemplateResponse", rendered)
    monkeypatch.setattr(auth, "_create_authenticated_session", authenticated)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="mfa-audit")
    app.include_router(auth.router)
    app.dependency_overrides[get_db] = lambda: db
    token = auth.create_2fa_token(user.id)
    with TestClient(app) as client:
        assert client.get("/mfa/setup", params={"token": token}).status_code == 200
        assert (
            client.post(
                "/mfa/setup",
                data={
                    "token_mfa": token,
                    "otp": pyotp.TOTP(user.otp_secret).now(),
                },
            ).status_code
            == 200
        )
    assert user.is_2fa_enabled
    authenticated.assert_called_once()


@pytest.mark.parametrize(
    "stored",
    [
        None,
        {},
        "bad-json",
        "{}",
        [None],
        ["bad"],
        [{"token": "t"}],
        [{"token": "t", "expires_at": 1}],
        [{"token": "t", "expires_at": "bad-date"}],
        [{"token": "t", "expires_at": "2020-01-01T00:00:00Z"}],
    ],
)
def test_malformed_web_tokens_fail_closed(stored):
    assert not is_valid_web_token("t", stored)


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("naive", [False, True])
def test_valid_web_tokens_accept_json_and_legacy_utc(serialized, naive):
    expiry = datetime.now(UTC) + timedelta(hours=1)
    if naive:
        expiry = expiry.replace(tzinfo=None)
    stored = [{"token": "t", "expires_at": expiry.isoformat()}]
    assert is_valid_web_token("t", json.dumps(stored) if serialized else stored)
    assert not is_valid_web_token("other", stored)


@pytest.mark.parametrize("role", [None, "viewer", "operator"])
@pytest.mark.parametrize("path", ["/reload-config", "/demo/toast/test-success"])
def test_admin_actions_reject_unprivileged_callers(monkeypatch, role, path):
    app = FastAPI()
    app.include_router(config.router)
    app.include_router(toast_demo.router)
    db = Mock()
    notify = Mock()
    monkeypatch.setattr(toast_demo.NotificationService, "success", notify)
    app.dependency_overrides[get_db] = lambda: db

    def current_user():
        if role is None:
            raise HTTPException(status_code=401)
        return SimpleNamespace(role=role)

    app.dependency_overrides[auth.get_current_user] = current_user
    with TestClient(app) as client:
        assert client.post(path).status_code == (401 if role is None else 403)
    notify.assert_not_called()
    db.commit.assert_not_called()


def test_ping_requires_operator_before_network_io(monkeypatch):
    network_ping = Mock()
    monkeypatch.setattr(ping, "ping", network_ping)
    app = FastAPI()
    app.include_router(ping.router)
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role="viewer")
    with TestClient(app) as client:
        assert client.get("/ping", params={"ip": "127.0.0.1"}).status_code == 403
    network_ping.assert_not_called()


@pytest.fixture
def media_api(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            Snapshot.__table__,
            Video.__table__,
        ],
    )
    with Session(engine) as db:
        allowed, denied = CameraGroup(id=101, name="Allowed"), CameraGroup(id=102, name="Denied")
        cameras = [
            Camera(id="allowed", hostname="allowed", groups=[allowed]),
            Camera(id="denied", hostname="denied", group_id=101, groups=[denied]),
        ]
        db.add_all([allowed, denied, *cameras])
        for model in (Snapshot, Video):
            for key, camera_id, deleted, held in [
                ("active", "allowed", False, False),
                ("denied", "denied", True, False),
                ("deleted", "allowed", True, False),
                ("held", "allowed", True, True),
                ("orphan", None, True, False),
            ]:
                row_id = f"{model.__name__}-{key}"
                db.add(
                    model(
                        id=row_id,
                        camera_id=camera_id,
                        camera_name=key,
                        camera_ip="127.0.0.1",
                        file_path=f"{row_id}.jpg",
                        resolution="640x480",
                        timestamp=datetime(2026, 10, 1, 23, 0),
                        deleted_at=datetime(2026, 10, 2) if deleted else None,
                        retention_hold=held,
                    )
                )
                (tmp_path / f"{row_id}.jpg").write_bytes(b"test footage")
        db.commit()
        user = SimpleNamespace(id=1, username="audit", role="admin", group_id=101)
        app = FastAPI(exception_handlers=main.app.exception_handlers.copy())
        app.add_middleware(SessionMiddleware, secret_key="media-audit")
        for router in (snapshots.router, snap_gallery.router, videos.router):
            app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[snapshots.get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        app.dependency_overrides[auth.user_access_required_optional] = lambda: user
        monkeypatch.setattr(snapshots, "SNAPSHOT_BASE_DIR", str(tmp_path))
        monkeypatch.setattr(videos, "VIDEO_FILESYSTEM_BASE", str(tmp_path))
        monkeypatch.setattr(snapshots, "log_audit", Mock())
        monkeypatch.setattr(snap_gallery, "log_audit", Mock())
        monkeypatch.setattr(videos, "log_audit", Mock())
        with TestClient(app) as client:
            yield client, db, user
    engine.dispose()


@pytest.mark.parametrize("key", ["denied", "orphan"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/snap/Snapshot-{key}/restore"),
        ("DELETE", "/admin/snapshots/Snapshot-{key}/purge"),
        ("GET", "/snap/Snapshot-{key}/verify"),
        ("POST", "/snap/Snapshot-{key}/retention-hold?enable=false"),
        ("DELETE", "/videos/Video-{key}"),
        ("POST", "/videos/Video-{key}/restore"),
        ("POST", "/videos/Video-{key}/retention-hold?enable=false"),
    ],
)
def test_media_mutations_and_integrity_respect_current_groups(media_api, key, method, path):
    client, db, _user = media_api
    assert client.request(method, path.format(key=key)).status_code == 403
    assert db.get(Snapshot, f"Snapshot-{key}").is_deleted
    assert db.get(Video, f"Video-{key}").is_deleted


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/admin/snapshots/Snapshot-held/purge", "DELETE"),
        ("/admin/snapshots/Snapshot-active/purge", "DELETE"),
        ("/videos/Video-held?hard_delete=true", "DELETE"),
        ("/videos/Video-active?hard_delete=true", "DELETE"),
        ("/admin/snapshots/purge?skip_retention_hold=false", "POST"),
        ("/admin/videos/purge?skip_retention_hold=false", "POST"),
    ],
)
def test_purge_cannot_override_holds_or_skip_soft_delete(media_api, path, method):
    client, db, _user = media_api
    assert client.request(method, path).status_code == 409
    assert db.query(Snapshot).count() == 5
    assert db.query(Video).count() == 5


def test_snapshot_service_honors_hold_even_for_legacy_callers(media_api, monkeypatch):
    _client, db, _user = media_api
    monkeypatch.setattr("app.utils.snapshot_service.os.remove", Mock())
    assert not asyncio.run(SnapshotService.delete_snapshot("Snapshot-held", db, hard_delete=True))
    assert (
        SnapshotService.purge_deleted_snapshots(
            db, days_old=0, skip_retention_hold=False, group_id=101
        )
        == 1
    )
    assert db.get(Snapshot, "Snapshot-held") is not None
    assert db.get(Snapshot, "Snapshot-denied") is not None
    assert db.get(Snapshot, "Snapshot-orphan") is not None


@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        ("/snapshot/token/Snapshot-active.jpg?token=arbitrary", "GET", None),
        ("/snapshot/token/batch", "POST", ["Snapshot-active.jpg"]),
        ("/snapshot/latest/allowed/info?token=arbitrary", "GET", None),
    ],
)
def test_snapshot_metadata_and_token_minting_require_authentication(media_api, path, method, body):
    client, _db, _user = media_api

    def unauthenticated():
        raise HTTPException(status_code=401)

    client.app.dependency_overrides[auth.get_current_user] = unauthenticated
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize(("key", "expected"), [("denied", 403), ("deleted", 404), ("active", 200)])
def test_snapshot_token_and_file_checks(media_api, key, expected):
    client, db, _user = media_api
    if key == "denied":
        db.get(Snapshot, "Snapshot-denied").deleted_at = None
        db.commit()
    response = client.get(f"/snapshot/token/Snapshot-{key}.jpg")
    assert response.status_code == expected
    assert (
        client.post("/snapshot/token/batch", json=[f"Snapshot-{key}.jpg"]).status_code == expected
    )
    assert client.get(f"/snapshot/file/Snapshot-{key}.jpg").status_code == expected


def test_signed_snapshot_link_survives_without_browser_session_but_not_deletion(media_api):
    client, db, _user = media_api
    token = client.get("/snapshot/token/Snapshot-active.jpg").json()["token"]
    client.app.dependency_overrides[auth.user_access_required_optional] = lambda: None
    path = "/snapshot/file/Snapshot-active.jpg"
    assert client.get(path).status_code == 401
    assert client.get(path, params={"token": "invalid"}).status_code == 403
    assert client.get(path, params={"token": token}).content == b"test footage"
    db.get(Snapshot, "Snapshot-active").soft_delete()
    db.commit()
    assert client.get(path, params={"token": token}).status_code == 404


def test_tamper_endpoint_has_one_handler_filters_groups_deletion_and_full_local_day(
    media_api, monkeypatch
):
    client, db, _user = media_api
    for row in db.query(Snapshot).all():
        row.is_tampered = True
    db.commit()
    monkeypatch.setattr("app.routes.snap_gallery.get_current_timezone", lambda _db: "Asia/Makassar")
    paths = [
        route
        for route in client.app.routes
        if getattr(route, "path", None) == "/snapshots/tampered"
    ]
    assert len(paths) == 1
    response = client.get("/snapshots/tampered")
    assert [row["id"] for row in response.json()] == ["Snapshot-active"]
    for day, expected in [("2026-10-01", []), ("2026-10-02", ["Snapshot-active"])]:
        response = client.get("/snapshots/tampered", params={"start": day, "end": day})
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == expected
    assert client.get("/snapshots/tampered?start=bad&end=bad").status_code == 400
    assert client.get("/snapshots/tampered?start=2026-10-01").status_code == 400


def test_optional_auth_rejects_stale_session_without_token_and_validates_bearer():
    request = Request({"type": "http", "headers": [], "session": {"user_id": 1}})
    db = Mock()
    assert asyncio.run(auth.user_access_required_optional(request, db)) is None
    db.query.assert_not_called()
    request = Request(
        {"type": "http", "headers": [(b"authorization", b"Bearer api")], "session": {}}
    )
    user = SimpleNamespace(
        api_tokens=[
            {"token": "api", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        ]
    )
    db.query.return_value.filter.return_value.all.return_value = [user]
    assert asyncio.run(auth.user_access_required_optional(request, db)) is user


def test_whatsapp_access_ignores_stale_primary_group(monkeypatch):
    handler = object.__new__(wa_webhook.WABotHandler)
    handler.db = Mock()
    handler._sender = "628123456789"
    monkeypatch.setattr(
        wa_webhook, "_whitelisted_sender", lambda *_args: SimpleNamespace(group_id=1)
    )
    camera = SimpleNamespace(group_id=1, groups=[SimpleNamespace(id=2)])
    with pytest.raises(HTTPException) as error:
        handler._require_camera_access(camera)
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/recipients"),
        ("POST", "/recipients/add"),
        ("POST", "/recipients/delete/1"),
        ("POST", "/api/recipients/test-send"),
        ("GET", "/api/whitelist"),
        ("GET", "/api/whitelist/group/628123456789"),
        ("GET", "/api/whatsapp/is-allowed?phone=628123456789"),
        ("GET", "/log/api"),
        ("POST", "/log/api"),
        ("GET", "/log/command"),
        ("POST", "/log/command"),
    ],
)
@pytest.mark.parametrize("role", [None, "viewer", "operator"])
def test_management_routers_require_admin_before_database_or_transport(method, path, role):
    app = FastAPI()
    for module in (group_recipients, whitelist, observability_log, whatsapp_routes):
        app.include_router(module.router)
    db = Mock()
    app.dependency_overrides[get_db] = lambda: db

    def current_user():
        if role is None:
            raise HTTPException(status_code=401)
        return SimpleNamespace(role=role)

    app.dependency_overrides[auth.get_current_user] = current_user
    with TestClient(app) as client:
        assert client.request(method, path).status_code == (401 if role is None else 403)
    db.query.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("path", ["/api/private", "/reload-config", "/login-extra"])
def test_arbitrary_query_token_cannot_bypass_session_middleware(path):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"token=arbitrary",
            "headers": [],
            "session": {},
            "scheme": "http",
            "server": ("test", 80),
        }
    )
    db = Mock()
    called = Mock()

    async def endpoint(_request):
        called()
        return JSONResponse({"secret": True})

    middleware = AuthAndSetupMiddleware(FastAPI())
    response = asyncio.run(middleware._do_dispatch(request, endpoint, db))
    assert response.status_code == 303
    called.assert_not_called()


@pytest.mark.parametrize(
    ("path", "headers"), [("/api/crash", {}), ("/page", {"Accept": "application/json"})]
)
def test_unhandled_api_errors_remain_generic_json(path, headers):
    app = FastAPI(exception_handlers=main.app.exception_handlers.copy())
    app.middleware("http")(main.error_wrapper_middleware)

    @app.get(path)
    def crash():
        raise RuntimeError("sensitive-internal-detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(path, headers=headers)
    assert response.status_code == 500
    assert response.json() == {"status": "error", "detail": "Internal Server Error"}
    assert "sensitive-internal-detail" not in response.text


@pytest.mark.parametrize("path", ["/api/unavailable", "/page"])
def test_503_status_and_retry_header_survive_error_wrapper(path):
    app = FastAPI(exception_handlers=main.app.exception_handlers.copy())
    app.add_middleware(SessionMiddleware, secret_key="error-audit")
    app.middleware("http")(main.error_wrapper_middleware)

    @app.get(path)
    def unavailable():
        raise HTTPException(
            status_code=503, detail="temporarily unavailable", headers={"Retry-After": "5"}
        )

    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    if path.startswith("/api/"):
        assert response.json()["detail"] == "temporarily unavailable"


def test_http_authentication_challenge_is_preserved():
    app = FastAPI(exception_handlers=main.app.exception_handlers.copy())

    @app.get("/api/challenge")
    def challenge():
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    with TestClient(app) as client:
        assert client.get("/api/challenge").headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("peer", "expected_ip", "expected_scheme"),
    [
        ("10.0.0.10", "192.0.2.8", "https"),
        ("198.51.100.1", "198.51.100.1", "http"),
    ],
)
def test_configured_proxy_trust_is_not_overridden(peer, expected_ip, expected_scheme):
    assert not any(
        item.cls.__name__ in {"RealIPFixMiddleware", "HTTPSProxyFixMiddleware"}
        for item in main.middleware
    )
    app = FastAPI()
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["10.0.0.10"])

    @app.get("/")
    def inspect_request(request: Request):
        return {"ip": request.client.host, "scheme": request.url.scheme}

    with TestClient(app, client=(peer, 1234)) as client:
        response = client.get(
            "/",
            headers={
                "X-Forwarded-For": "192.0.2.8",
                "X-Forwarded-Proto": "https",
                "X-Real-IP": "203.0.113.7",
            },
        )
    assert response.json() == {"ip": expected_ip, "scheme": expected_scheme}


def test_session_cookie_uses_auth_configuration():
    session = next(item for item in main.middleware if item.cls is SessionMiddleware)
    assert session.kwargs["https_only"] == auth.COOKIE_SECURE
    assert session.kwargs["same_site"] == auth.COOKIE_SAMESITE
    assert session.kwargs["max_age"] == auth.SESSION_MAX_AGE_SECONDS


def test_camera_deletion_preserves_video_evidence_and_hold(monkeypatch):
    video = SimpleNamespace(file_path="evidence.mp4", retention_hold=True)
    camera = SimpleNamespace(hostname="Test", videos=[video])
    db = Mock()
    db.query.return_value.options.return_value.filter.return_value.first.return_value = camera
    remove = Mock()
    monkeypatch.setattr(cameras.os, "remove", remove)
    monkeypatch.setattr(cameras, "log_audit", Mock())
    request = Request({"type": "http", "session": {}, "headers": [], "client": ("127.0.0.1", 1)})
    response = asyncio.run(cameras.delete_camera(request, "camera", db))
    assert response.status_code == 200
    remove.assert_not_called()
    assert "delete" not in Camera.videos.property.cascade
    assert "delete-orphan" not in Camera.videos.property.cascade
    assert next(iter(Video.__table__.c.camera_id.foreign_keys)).ondelete == "SET NULL"


def test_camera_deletion_preserves_actual_video_rows_hash_and_hold(media_api, monkeypatch):
    _client, db, _user = media_api
    Base.metadata.create_all(
        db.get_bind(),
        tables=[
            Base.metadata.tables[name]
            for name in (
                "nvr",
                "snapshot_logs",
                "camera_health",
                "camera_daily_stats",
                "camera_email_notification_logs",
                "camera_email_notification_log_recipients",
                "camera_status_change_log",
                "sla_reports",
            )
        ],
    )
    db.execute(text("PRAGMA foreign_keys=ON"))
    held = db.get(Video, "Video-held")
    held.file_hash = "a" * 64
    db.commit()
    remove = Mock()
    monkeypatch.setattr(cameras.os, "remove", remove)
    monkeypatch.setattr(cameras, "log_audit", Mock())
    request = Request({"type": "http", "session": {}, "headers": [], "client": ("127.0.0.1", 1)})
    assert asyncio.run(cameras.delete_camera(request, "allowed", db)).status_code == 200
    db.expire_all()
    assert db.get(Camera, "allowed") is None
    held = db.get(Video, "Video-held")
    assert held.camera_id is None
    assert held.file_hash == "a" * 64
    assert held.retention_hold
    assert db.query(Video).count() == 5
    assert db.get(Snapshot, "Snapshot-held").camera_id is None
    remove.assert_not_called()


@pytest.mark.parametrize(
    ("model", "route"),
    [(Snapshot, "/snap/Snapshot-deleted/restore"), (Video, "/videos/Video-deleted/restore")],
)
def test_authorized_restore_still_works(media_api, model, route):
    client, db, _user = media_api
    assert client.post(route).status_code == 200
    assert not db.get(model, f"{model.__name__}-deleted").is_deleted


@pytest.mark.parametrize(
    ("credentials", "origin", "allowed"),
    [
        (False, None, False),
        (True, None, True),
        (True, "http://testserver", True),
        (True, "https://external.example", False),
        (True, "http://[malformed", False),
    ],
)
def test_websocket_handshake_requires_valid_browser_session_and_origin(
    monkeypatch, credentials, origin, allowed
):
    user = SimpleNamespace(
        web_tokens=[
            {"token": "browser", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        ]
    )
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=Mock())
    factory.return_value.__exit__ = Mock(return_value=False)
    db = factory.return_value.__enter__.return_value
    db.query.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr(record_notifications, "SessionLocal", factory)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="socket-audit")
    app.include_router(websocket_routes.router)

    @app.get("/session")
    def session(request: Request):
        request.session["user_id"] = 7
        response = JSONResponse({"ok": True})
        response.set_cookie("session_token", "browser")
        return response

    with TestClient(app) as client:
        if credentials:
            client.get("/session")
        headers = {"Origin": origin} if origin else {}
        if allowed:
            with client.websocket_connect("/ws", headers=headers) as socket:
                socket.send_text("heartbeat")
        else:
            with (
                pytest.raises(WebSocketDisconnect) as error,
                client.websocket_connect("/ws", headers=headers),
            ):
                pass
            assert error.value.code == 1008
