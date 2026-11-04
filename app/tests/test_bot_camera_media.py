"""Media discovery uses a SQLite-compatible subset; bot transport is mocked."""

from __future__ import annotations

import sys

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import Base, get_db
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.config import Configuration
from app.models.snapshot import Snapshot
from app.models.whitelist import WhatsappWhitelist
from app.routes import auth, bot_camera_media, wa_webhook
from app.utils.wa_bot_commands import get_command_settings, save_command_settings
from app.utils.wa_bot_workflow import APIFlowError


@pytest.fixture
def media_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "static/snapshots"
    root.mkdir(parents=True)
    (root / "new.jpg").write_bytes(b"image")
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            Snapshot.__table__,
            WhatsappWhitelist.__table__,
            Configuration.__table__,
        ],
    )
    with Session(engine) as db:
        monkeypatch.setattr(bot_camera_media, "log_audit", Mock())
        first, second = CameraGroup(id=101, name="Site A"), CameraGroup(id=102, name="Site B")
        db.add_all(
            [
                first,
                second,
                Camera(
                    id="shared",
                    hostname="Gate 1",
                    status="Active",
                    location="North Yard",
                    groups=[first, second],
                ),
                Camera(
                    id="other",
                    hostname="Gate 2",
                    status="Active",
                    location="North Yard",
                    group_id=101,
                    groups=[second],
                ),
                Camera(
                    id="missing",
                    hostname="Gate 3",
                    status="Active",
                    location="North Yard",
                    groups=[first],
                ),
                WhatsappWhitelist(phone_number="+628123456789", group_id=101, role="admin"),
                Configuration(key="app_public_url", value="https://bsnap.example"),
                Configuration(key="map_live_enabled", value="1"),
            ]
        )
        db.flush()
        db.add_all(
            [
                Snapshot(
                    id="older",
                    camera_id="shared",
                    camera_name="Gate 1",
                    camera_ip="",
                    file_path="old.jpg",
                    timestamp=datetime(2026, 11, 1),
                ),
                Snapshot(
                    id="new",
                    camera_id="shared",
                    camera_name="Gate 1",
                    camera_ip="",
                    file_path="new.jpg",
                    timestamp=datetime(2026, 11, 2),
                ),
                Snapshot(
                    id="deleted",
                    camera_id="shared",
                    camera_name="Gate 1",
                    camera_ip="",
                    file_path="deleted.jpg",
                    timestamp=datetime(2026, 11, 3),
                    deleted_at=datetime.now(UTC),
                ),
                Snapshot(
                    id="denied",
                    camera_id="other",
                    camera_name="Gate 2",
                    camera_ip="",
                    file_path="new.jpg",
                ),
            ]
        )
        db.commit()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="isolated-test-session-key")
        app.include_router(bot_camera_media.router)
        user = SimpleNamespace(id=1, username="test", role="admin", group_id=None)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, db=db, user=user, root=root)
    engine.dispose()


def get_snapshots(fixture, **params):
    return fixture.client.get(
        "/api/bot/cameras/latest-snapshots",
        params={
            "phone_number": "628123456789",
            "scope": "location",
            "keyword": "north yard",
            **params,
        },
    )


def test_latest_per_camera_and_missing_feedback(media_client):
    response = get_snapshots(media_client)
    assert response.status_code == 200
    body = response.json()
    assert body["total_cameras"] == 2
    assert [row["snapshot_id"] for row in body["results"]] == ["new"]
    assert body["results"][0]["timestamp"].endswith("+00:00")
    assert body["results"][0]["url"] == "/api/snapshots/secure/new"
    assert body["missing"] == ["Gate 3"]


def test_membership_intersection_and_exact_literal_selection(media_client):
    media_client.user.group_id = 102
    body = get_snapshots(media_client, scope="group", keyword="SITE A").json()
    assert body["total_cameras"] == 1
    assert body["missing"] == []
    assert get_snapshots(media_client, keyword="%").status_code == 404
    assert get_snapshots(media_client, keyword="  ").status_code == 422
    assert get_snapshots(media_client, phone_number="12025550123").status_code == 403
    media_client.user.group_id = 999
    assert get_snapshots(media_client).status_code == 404


def test_pagination_and_missing_or_escaping_files(media_client):
    first = get_snapshots(media_client, per_page=1).json()
    assert first["has_more"] is True
    second = get_snapshots(media_client, per_page=1, page=2).json()
    assert second["has_more"] is False
    assert second["missing"] == ["Gate 3"]
    snapshot = media_client.db.get(Snapshot, "new")
    snapshot.file_path = "../../escape.jpg"
    media_client.db.commit()
    body = get_snapshots(media_client).json()
    assert body["results"] == []
    assert body["missing"] == ["Gate 1", "Gate 3"]
    assert get_snapshots(media_client, per_page=51).status_code == 422


def test_live_link_preserves_admin_policy_and_public_origin(media_client):
    def request(**params):
        return media_client.client.get(
            "/api/bot/cameras/live-link",
            params={
                "keyword": "Gate 1",
                "phone_number": "628123456789",
                **params,
            },
        )

    body = request().json()
    assert body["url"].startswith("https://bsnap.example/public/cameras/shared/live?token=")
    assert body["requires_login"] is False
    assert body["expires_in"] == 300
    assert body["expires_at"].endswith("+00:00")
    assert request(expires_in=29).status_code == 422
    assert request(expires_in=901).status_code == 422
    assert request(expires_in=30).json()["expires_in"] == 30
    assert request(keyword="Gate 2").status_code == 404
    media_client.user.role = "viewer"
    assert request().status_code == 403
    assert get_snapshots(media_client).status_code == 200
    media_client.user.role = "admin"
    entry = media_client.db.query(WhatsappWhitelist).first()
    entry.role = "user"
    media_client.db.commit()
    assert request().status_code == 403
    entry.role = "admin"
    media_client.db.get(Configuration, "map_live_enabled").value = "0"
    media_client.db.commit()
    assert request().status_code == 403
    media_client.db.get(Configuration, "map_live_enabled").value = "1"
    media_client.db.get(Configuration, "app_public_url").value = "https://user:secret@example.com"
    media_client.db.commit()
    assert request().status_code == 409


@pytest.mark.parametrize("action", ["live", "snap_group", "snap_location"])
def test_command_settings_round_trip(action):
    db = Mock()
    row = SimpleNamespace(value="[]")
    db.query.return_value.filter.return_value.first.return_value = row
    result = save_command_settings(
        db,
        [
            {
                "id": "custom_media",
                "trigger": "/media",
                "action": action,
                "response": "{{steps.result.body.camera}}",
                "image_hd": True,
            }
        ],
    )[0]
    assert result["role"] == ("admin" if action == "live" else "user")
    assert result["image_hd"] is (action != "live")
    assert get_command_settings(db)[0]["action"] == action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "scope"), [("snap_group", "group"), ("snap_location", "location")]
)
async def test_bot_requests_all_pages_and_attaches_each_image(monkeypatch, action, scope):
    bot = wa_webhook.WABotHandler(Mock())
    monkeypatch.setattr(bot, "_selected_api_token", lambda: "test-token")
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Makassar")
    attach = AsyncMock()
    monkeypatch.setattr(bot, "_attach_response_image", attach)
    flow = AsyncMock(
        side_effect=[
            {
                "steps": {
                    "result": {
                        "body": {
                            "results": [
                                {
                                    "camera": "A",
                                    "url": "/api/snapshots/secure/a",
                                    "timestamp": "2026-11-03T17:00:00Z",
                                }
                            ],
                            "missing": [],
                            "has_more": True,
                        }
                    }
                }
            },
            {
                "steps": {
                    "result": {
                        "body": {
                            "results": [
                                {
                                    "camera": "B",
                                    "url": "/api/snapshots/secure/b",
                                    "timestamp": "2026-11-03T17:00:00Z",
                                }
                            ],
                            "missing": ["C"],
                            "has_more": False,
                        }
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(wa_webhook, "run_api_flow", flow)
    assert (
        await bot._handle_camera_media_api(
            action,
            "+628123456789",
            "North Yard",
            {
                "trigger": "/media",
                "response": "{{#each steps.result.body.results}}{{@index1}}. {{this.camera}} | {{this.timestamp|datetime}}{{/each}}",
            },
        )
        == ""
    )
    assert attach.await_count == 2
    assert "04/11/2026 - 01:00:00 WITA" in attach.call_args.args[1]
    assert attach.call_args_list[0].args[1].startswith("1. A |")
    assert attach.call_args_list[1].args[1].startswith("2. B |")
    assert bot.outbound_items == [{"text": "Latest snapshot unavailable: C"}]
    params = flow.call_args.kwargs["flow"][0]["params"]
    assert params == {
        "keyword": "North Yard",
        "phone_number": "+628123456789",
        "scope": scope,
        "page": 2,
    }
    assert not bot.action_failed


@pytest.mark.asyncio
async def test_bot_requires_token_and_keeps_api_errors_credential_safe(monkeypatch):
    bot = wa_webhook.WABotHandler(Mock())
    monkeypatch.setattr(bot, "_selected_api_token", lambda: None)
    result = await bot._handle_camera_media_api("live", "phone", "camera", {"trigger": "/live"})
    assert "API token" in result
    monkeypatch.setattr(bot, "_selected_api_token", lambda: "token")
    monkeypatch.setattr(wa_webhook, "run_api_flow", AsyncMock(side_effect=ValueError("secret")))
    result = await bot._handle_camera_media_api("live", "phone", "camera", {"trigger": "/live"})
    assert "secret" not in result
    assert bot.action_failed


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_live_command_dispatch_enforces_whitelist_role(monkeypatch, role):
    bot = wa_webhook.WABotHandler(Mock())
    monkeypatch.setattr(
        wa_webhook,
        "_whitelisted_sender",
        lambda *_args: SimpleNamespace(role=role, group_id=None),
    )
    monkeypatch.setattr(bot, "_selected_api_token", lambda: "test-token")
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Makassar")
    flow = AsyncMock(
        return_value={
            "steps": {
                "result": {
                    "body": {
                        "camera": "Gate 1",
                        "url": "https://bsnap.example/maps/cameras/shared/live",
                    }
                }
            }
        }
    )
    monkeypatch.setattr(wa_webhook, "run_api_flow", flow)
    bot._test_command = {"action": "live", "trigger": "/live", "role": "user"}
    bot._test_argument = "Gate 1"
    result = await bot.handle("628123456789", "/live Gate 1")
    if role == "admin":
        assert "https://bsnap.example/maps/cameras/shared/live" in result
        assert not bot.action_failed
    else:
        assert "admins only" in result
        flow.assert_not_called()


def test_discovery_requires_authentication(media_client):
    media_client.client.app.dependency_overrides.pop(auth.get_current_user)
    assert get_snapshots(media_client).status_code == 401


@pytest.fixture
def action_client(media_client, monkeypatch):
    """Exercise the real test-action -> API -> protected attachment path in isolation."""
    app = media_client.client.app
    app.include_router(wa_webhook.router)
    media_client.user.api_tokens = [{"id": "test-id", "token": "isolated-test-token"}]
    monkeypatch.setitem(sys.modules, "app.main", SimpleNamespace(app=app))
    monkeypatch.setenv("TRUSTED_HOSTS", "testserver")
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Makassar")
    monkeypatch.setattr(wa_webhook, "log_audit", Mock())
    return media_client


def execute_action(fixture, action, argument, response=""):
    return fixture.client.post(
        "/api/admin/wa-bot/test-action",
        json={
            "command": {"action": action, "response": response},
            "argument": argument,
            "sender": "628123456789",
            "api_token_id": "test-id",
        },
    )


def test_live_execute_uses_public_origin_fallback(action_client, monkeypatch):
    action_client.db.get(Configuration, "app_public_url").value = ""
    action_client.db.commit()
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://public.example")
    response = execute_action(action_client, "live", "Gate 1")
    assert response.status_code == 200
    assert "https://public.example/public/cameras/shared/live?token=" in response.json()["response_preview"]


def test_live_execute_explains_missing_origin_without_generic_gateway_error(
    action_client, monkeypatch
):
    action_client.db.get(Configuration, "app_public_url").value = ""
    action_client.db.commit()
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    response = execute_action(action_client, "live", "Gate 1")
    assert response.status_code == 409
    assert response.json()["detail"] == "Configure a valid Public B-Snap URL in Configuration"


@pytest.mark.parametrize(
    ("action", "argument"), [("snap_group", "Site A"), ("snap_location", "North Yard")]
)
@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("Custom caption: {{camera}}", ["Custom caption: Gate 1", "Custom caption: Gate 4"]),
        (
            "{{#each steps.result.body.results}}{{@index1}}. {{this.camera}}{{/each}}",
            ["1. Gate 1", "2. Gate 4"],
        ),
    ],
)
def test_execute_previews_custom_caption_attached_to_each_image(
    action_client, action, argument, template, expected
):
    action_client.db.add(
        Camera(
            id="another",
            hostname="Gate 4",
            location="North Yard",
            groups=[action_client.db.get(CameraGroup, 101)],
        )
    )
    action_client.db.flush()
    action_client.db.add(
        Snapshot(
            id="another-snapshot",
            camera_id="another",
            camera_name="Gate 4",
            camera_ip="",
            file_path="new.jpg",
        )
    )
    action_client.db.commit()
    response = execute_action(action_client, action, argument, template)
    assert response.status_code == 200
    body = response.json()
    assert body["response_previews"][0] == {
        "stage": "success",
        "text": expected[0],
        "media_type": "image",
        "source": "Snapshot from action result",
    }
    assert expected[0] in body["response_preview"]
    assert body["response_previews"][1]["media_type"] == "image"
    assert body["response_previews"][1]["text"] == expected[1]
    assert body["response_previews"][2]["media_type"] == "text"
    assert "static/snapshots" not in response.text


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422])
def test_camera_api_errors_keep_status_and_hide_arbitrary_diagnostics(status):
    error = APIFlowError(
        "unsafe-secret",
        {
            "steps": {
                "result": {
                    "status_code": status,
                    "body": {"detail": "unsafe-secret"},
                }
            }
        },
    )
    message, actual_status = wa_webhook.WABotHandler._camera_media_api_error(error)
    assert actual_status == status
    assert "unsafe-secret" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "argument"), [("snap_group", "Site A"), ("snap_location", "North Yard")]
)
async def test_mapper_caption_reaches_direct_bot_image_delivery(
    action_client, monkeypatch, action, argument
):
    template = "{{#each steps.result.body.results}}\n{{@index1}}. {{this.camera}} | {{this.timestamp}}\n{{/each}}"
    command = {
        "id": "custom_batch",
        "action": action,
        "trigger": "/batch",
        "enabled": True,
        "response": template,
    }
    monkeypatch.setattr(wa_webhook, "get_command_settings", lambda _db: [command])
    bot = wa_webhook.WABotHandler(action_client.db)
    monkeypatch.setattr(bot, "_selected_api_token", lambda: "isolated-test-token")
    await bot.handle("628123456789", f"/batch {argument}")
    assert not bot.action_failed
    image = next(item for item in bot.outbound_items if item.get("image_path"))
    assert image["text"].strip() == "1. Gate 1 | 2026-11-02T00:00:00+00:00"
    gateway = Mock()
    wa_webhook._send_wa_message_item(gateway, "628123456789", image)
    assert gateway.send_image_file.call_args.args[2] == image["text"]


@pytest.mark.asyncio
async def test_batch_retains_prepared_images_on_later_api_failure(monkeypatch):
    bot = wa_webhook.WABotHandler(Mock())
    monkeypatch.setattr(bot, "_selected_api_token", lambda: "test-token")
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Jakarta")

    async def attach(_command, caption):
        bot.outbound_items.append({"image_path": "first.jpg", "text": caption})

    monkeypatch.setattr(bot, "_attach_response_image", attach)
    monkeypatch.setattr(
        wa_webhook,
        "run_api_flow",
        AsyncMock(
            side_effect=[
                {
                    "steps": {
                        "result": {
                            "body": {
                                "results": [
                                    {
                                        "camera": "A",
                                        "url": "/api/snapshots/secure/a",
                                        "timestamp": "2026-11-03T17:00:00Z",
                                    }
                                ],
                                "missing": [],
                                "has_more": True,
                            }
                        }
                    }
                },
                ValueError("unavailable"),
            ]
        ),
    )
    await bot._handle_camera_media_api("snap_group", "phone", "Site A", {"trigger": "/snapgroup"})
    assert bot.outbound_items[0]["image_path"] == "first.jpg"
    assert "request failed" in bot.outbound_items[1]["text"]
    assert bot.action_failed
