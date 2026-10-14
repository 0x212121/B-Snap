"""HD delivery tests use mocked persistence and transport; no WA messages are sent."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.jobs import wa_command_schedule
from app.routes import wa_webhook
from app.utils import wa_executor, wa_gateway
from app.utils.wa_bot_commands import (
    get_command_settings,
    normalize_image_hd,
    save_command_settings,
)
from app.utils.wa_gateway import WAGatewayService


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema creation because these tests use mock sessions."""


@pytest.mark.parametrize("action", ["snap", "cctv"])
@pytest.mark.parametrize("hd", [False, True])
def test_quality_setting_round_trip(action: str, hd: bool) -> None:
    db = Mock()
    row = SimpleNamespace(value="[]")
    db.query.return_value.filter.return_value.first.return_value = row
    command = {
        "id": "custom_snapshot",
        "trigger": "/camera",
        "action": action,
        "name": "Camera",
        "response": "{{camera}}",
        "image_hd": hd,
    }
    assert save_command_settings(db, [command])[0]["image_hd"] is hd
    assert get_command_settings(db)[0]["image_hd"] is hd
    command.pop("image_hd")
    assert save_command_settings(db, [command])[0]["image_hd"] is False


def test_quality_validation() -> None:
    for invalid in ("false", "true", 0, 1, None):
        with pytest.raises(ValueError, match="boolean"):
            normalize_image_hd("snap", invalid)
    assert normalize_image_hd("text", True) is False


@pytest.mark.parametrize("hd", [False, True])
@pytest.mark.parametrize("source", ["image_path", "image_url"])
def test_image_delivery_preserves_reply_and_quality(hd: bool, source: str) -> None:
    service = Mock()
    item = {source: "snapshot", "text": "Gate", "image_hd": hd}
    wa_webhook._send_wa_message_item(service, "chat@g.us", item, "reply-id")
    method = service.send_image_file if source == "image_path" else service.send_image
    method.assert_called_once_with(
        "chat@g.us", "snapshot", "Gate", reply_to="reply-id", **({"hd": True} if hd else {})
    )


@pytest.mark.parametrize("hd", [False, True])
def test_gateway_multipart_quality(tmp_path, hd: bool) -> None:
    service = WAGatewayService.__new__(WAGatewayService)
    service.config = SimpleNamespace(
        is_configured=lambda: True, base_url="http://gowa-test", device_id=None
    )
    service._post_with_retry = Mock(return_value=Mock(status_code=200))
    image = tmp_path / "snapshot.jpg"
    image.write_bytes(b"mock image")
    assert service.send_image_file("628123456789", str(image), "Gate", "reply-id", hd=hd)["success"]
    data = service._post_with_retry.call_args.kwargs["data"]
    assert data["reply_message_id"] == "reply-id"
    assert data.get("hd") == ("true" if hd else None)
    service._post_with_retry.reset_mock()
    assert service.send_image(
        "628123456789", "https://example.test/image.jpg", "Gate", "reply-id", hd=hd
    )["success"]
    fields = service._post_with_retry.call_args.kwargs["files"]
    assert fields["reply_message_id"] == (None, "reply-id")
    assert fields.get("hd") == ((None, "true") if hd else None)


@pytest.mark.parametrize("hd", [False, True])
def test_snapshot_fallback_quality(monkeypatch, hd: bool) -> None:
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Makassar")
    bot = wa_webhook.WABotHandler(Mock())
    bot.image_hd = hd
    monkeypatch.setattr(bot, "_snapshot_template_data", lambda *_args: {})
    items = bot._render_snap_stage_messages(
        [{"text": "Fallback", "image_source_type": "last_snapshot"}],
        {"steps": {}},
        SimpleNamespace(hostname="Gate", ip=""),
        "Gate",
        "628123456789",
        SimpleNamespace(file_path="fallback.jpg"),
    )
    assert items[0]["image_path"].endswith("fallback.jpg")
    assert items[0].get("image_hd", False) is hd


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["snap", "cctv"])
@pytest.mark.parametrize("hd", [False, True])
async def test_snapshot_action_quality(monkeypatch, action: str, hd: bool) -> None:
    monkeypatch.setattr(
        wa_webhook, "_whitelisted_sender", lambda *_args: SimpleNamespace(role="user")
    )
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: "Asia/Makassar")
    monkeypatch.setattr(wa_webhook.os.path, "isfile", lambda _path: True)
    bot = wa_webhook.WABotHandler(Mock())
    bot.db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        status="Online"
    )
    camera = SimpleNamespace(id="cam", hostname="Gate", ip="192.168.1.10")
    snapshot = SimpleNamespace(file_path="gate.jpg")
    monkeypatch.setattr(bot, "_find_camera_for_capture", lambda _arg: camera)
    monkeypatch.setattr(
        bot, "_find_latest_camera_snapshots_by_hostname_prefix", lambda _arg: [(camera, snapshot)]
    )
    monkeypatch.setattr(bot, "_require_camera_access", lambda _camera: None)
    monkeypatch.setattr(bot, "_latest_snapshot_with_file", lambda _id: snapshot)
    monkeypatch.setattr(bot, "_snapshot_template_data", lambda *_args: {"camera": "Gate"})
    monkeypatch.setattr(bot, "_build_snapshot_caption", lambda *_args: "Gate")
    monkeypatch.setattr(bot, "_schedule_processing_items", AsyncMock())
    monkeypatch.setattr(bot, "_schedule_action_processing", AsyncMock())
    monkeypatch.setattr(
        wa_webhook.SnapshotService, "capture_snapshot", AsyncMock(return_value=snapshot)
    )
    bot._test_command = {
        "action": action,
        "trigger": "/camera",
        "response": "{{camera}}",
        "image_hd": hd,
    }
    bot._test_argument = "Gate"
    await bot.handle("628123456789", "/camera Gate")
    assert not bot.action_failed
    assert bot.image_hd is hd
    if action == "snap":
        assert bot.outbound_items[-1].get("image_hd", False) is hd
    else:
        assert bot.media_items[0]["path"].endswith("gate.jpg")


@pytest.mark.asyncio
@pytest.mark.parametrize("hd", [False, True])
async def test_scheduled_latest_snapshot_quality(monkeypatch, hd: bool) -> None:
    bot = wa_webhook.WABotHandler(Mock())
    bot.image_hd = hd
    bot.media_items = [{"path": "gate.jpg", "caption": "Gate"}]
    bot.handle = AsyncMock(return_value="")
    monkeypatch.setattr(wa_webhook, "WABotHandler", lambda _db: bot)
    monkeypatch.setattr(wa_webhook, "_whitelisted_sender", lambda *_args: object())
    monkeypatch.setattr(wa_webhook, "_command_access_error", lambda *_args: "")
    monkeypatch.setattr(wa_webhook, "_record_wa_message", Mock())
    monkeypatch.setattr(
        wa_gateway,
        "WAGatewayService",
        lambda _db: SimpleNamespace(config=SimpleNamespace(enabled=True)),
    )
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(wa_executor, "run_gateway_blocking", send)
    command = {
        "action": "cctv",
        "trigger": "/camera",
        "schedule": {"recipient": "628123456789", "argument": "Gate"},
    }
    assert await wa_command_schedule.execute_scheduled_command(Mock(), command) == 1
    assert send.await_args.args[3].get("image_hd", False) is hd
