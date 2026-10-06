"""Tests for configurable WhatsApp action processing and fallback messages."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import event

from app.utils.wa_bot_commands import CUSTOM_ACTION_ROLES, get_command_settings, save_command_settings
from app.routes.wa_webhook import WABotHandler
from app.utils.wa_gateway import WAGatewayService
from app.models.audit_log import AuditLog, create_audit_log_triggers
from app.utils.wa_bot_commands import normalize_response_media
from app.utils.wa_bot_commands import normalize_processing_delay
import asyncio


# The shared test database is SQLite, while a few production models use JSONB.
@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


# The project-wide SQLite fixture cannot create PostgreSQL-only audit triggers.
event.remove(AuditLog.__table__, "after_create", create_audit_log_triggers)


def _stage(text: str) -> list[dict[str, str]]:
    return [{"text": text, "image_source_type": "none", "image_source": ""}]


def test_processing_delay_settings_round_trip(db_session):
    command = {"id": "custom_delayed", "name": "Delayed", "trigger": "/delayed",
               "action": "text", "response": "Done", "processing_delay_seconds": 5}
    save_command_settings(db_session, [command])
    assert get_command_settings(db_session)[0]["processing_delay_seconds"] == 5
    command.pop("processing_delay_seconds")
    save_command_settings(db_session, [command])
    assert get_command_settings(db_session)[0]["processing_delay_seconds"] == 0
    for invalid in (-1, 61, 1.5, "3", True):
        with pytest.raises(ValueError):
            normalize_processing_delay(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,expected", [("missing", 404), ("offline", 503), ("capture_failed", 502), ("success", 200)])
async def test_live_snapshot_action_returns_success_and_error_fields(db_session, monkeypatch, scenario, expected):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.models.camera import Camera
    from app.models.health import CameraHealth
    from app.models.snapshot import Snapshot
    from app.routes import wa_webhook

    camera = Camera(hostname="capture-test", ip="192.168.1.5", status="Active")
    if scenario != "missing":
        db_session.add(camera)
        db_session.flush()
        db_session.add(CameraHealth(camera_id=camera.id, status="Offline" if scenario == "offline" else "Online"))
        db_session.flush()
    snapshot = Snapshot(id="new-snapshot", camera_id=camera.id, camera_name=camera.hostname,
                        camera_ip=camera.ip, file_path="test-capture.jpg")
    capture = AsyncMock(return_value=snapshot if scenario == "success" else None)
    monkeypatch.setattr(wa_webhook.SnapshotService, "capture_snapshot", capture)
    original_isfile = wa_webhook.os.path.isfile
    monkeypatch.setattr(wa_webhook.os.path, "isfile", lambda path: str(path).endswith("test-capture.jpg") or original_isfile(path))
    request = SimpleNamespace(json=AsyncMock(return_value={
        "command": {"action": "snap", "response": "Snapshot {{steps.result.body.0.camera}}",
                    "required_params": ["hostname"], "failure_messages": _stage("Failed: {{steps.error.body.message}}")},
        "argument": "capture-test", "execute": True,
    }))
    admin = SimpleNamespace(id=1, username="admin", role="admin", group_id=None, api_tokens=[])
    result = await wa_webhook.test_wa_bot_action(request, db_session, admin)
    payload = json.loads(result.body)
    assert result.status_code == expected
    if expected == 200:
        assert payload["status"] == "success"
        assert payload["steps"]["result"]["body"][0]["camera"] == "capture-test"
        assert payload["response_preview"] == "Snapshot capture-test"
    else:
        assert payload["status"] == "error"
        assert payload["steps"]["error"]["status_code"] == expected
        assert payload["steps"]["error"]["body"]["message"]
        assert payload["failure_previews"][0]["text"].startswith("Failed: ")
    if scenario in {"missing", "offline"}:
        capture.assert_not_awaited()
    else:
        capture.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_native_tests_require_explicit_execution_for_mutations(db_session):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.routes import wa_webhook

    for action in ("snap", "edit", "whitelist_add", "whitelist_remove"):
        request = SimpleNamespace(json=AsyncMock(return_value={"command": {"action": action}}))
        with pytest.raises(HTTPException) as raised:
            await wa_webhook.test_wa_bot_action(request, db_session, SimpleNamespace(role="admin"))
        assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_live_whitelist_tests_return_success_and_validation_errors(db_session):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.models.whitelist import WhatsappWhitelist
    from app.routes import wa_webhook

    admin = SimpleNamespace(id=1, role="admin", username="admin", group_id=None, api_tokens=[])
    for action in ("whitelist_add", "whitelist_remove"):
        request = SimpleNamespace(json=AsyncMock(return_value={
            "command": {"action": action, "response": "{{steps.result.body.action}}"},
            "argument": "628123456789", "sender": "628111111111", "execute": True,
        }))
        result = await wa_webhook.test_wa_bot_action(request, db_session, admin)
        payload = json.loads(result.body)
        assert result.status_code == 200
        assert payload["steps"]["result"]["body"]["success"]
        assert payload["response_preview"] == ("added" if action == "whitelist_add" else "removed")
    assert db_session.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == "628123456789").first() is None
    request = SimpleNamespace(json=AsyncMock(return_value={
        "command": {"action": "whitelist_add", "response": "Added"}, "argument": "invalid", "execute": True,
    }))
    result = await wa_webhook.test_wa_bot_action(request, db_session, admin)
    assert result.status_code == 400
    assert json.loads(result.body)["steps"]["error"]["body"]["message"]


@pytest.mark.asyncio
async def test_live_ping_and_gateway_errors_have_failure_variables(db_session, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.routes import wa_webhook

    monkeypatch.setitem(sys.modules, "ping3", SimpleNamespace(ping=lambda *args, **kwargs: None))
    monkeypatch.setattr(wa_webhook, "WAGatewayService", lambda db: SimpleNamespace(check_connection=lambda: {
        "connected": False, "error": "Gateway unavailable",
    }))
    admin = SimpleNamespace(id=1, role="admin", username="admin", group_id=None, api_tokens=[])
    for action, expected in (("ping", 504), ("bot_status", 503)):
        request = SimpleNamespace(json=AsyncMock(return_value={
            "command": {"action": action, "response": "Result"}, "argument": "host",
        }))
        result = await wa_webhook.test_wa_bot_action(request, db_session, admin)
        payload = json.loads(result.body)
        assert result.status_code == expected
        assert payload["status"] == "error"
        assert payload["steps"]["error"]["body"]["message"]


def test_delayed_progress_uses_its_own_worker_session(monkeypatch):
    from unittest.mock import Mock
    from app.routes import wa_webhook

    session = Mock()
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=session)
    factory.return_value.__exit__ = Mock(return_value=False)
    gateway = Mock()
    gateway.send_text.return_value = {"success": True}
    log = Mock()
    monkeypatch.setattr(wa_webhook, "SessionLocal", factory)
    monkeypatch.setattr(wa_webhook, "WAGatewayService", Mock(return_value=gateway))
    monkeypatch.setattr(wa_webhook, "_record_wa_message", log)
    assert wa_webhook._send_wa_progress_item("recipient", {"text": "Processing"}, "sender", "/record", None)["success"]
    assert log.call_args.args[0] is session
    session.commit.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_fast_result_cancels_delayed_processing(db_session, monkeypatch, failed):
    from unittest.mock import AsyncMock

    save_command_settings(db_session, [{
        "id": "custom_delay", "name": "Delay", "trigger": "/delay", "action": "ping",
        "response": "Done", "processing_delay_seconds": 5,
        "processing_messages": _stage("Processing"), "failure_messages": _stage("Fallback"),
    }])
    handler = WABotHandler(db_session)
    handler.progress_sender = AsyncMock(return_value={"success": True})

    async def complete(*args):
        handler.action_failed = failed
        return "Done"

    monkeypatch.setattr(handler, "_handle_n8n_action", complete)
    response = await handler.handle("628123456789", "/delay host")
    texts = [call.args[0]["text"] for call in handler.progress_sender.call_args_list]
    assert texts == (["Fallback"] if failed else [])
    assert response == ("" if failed else "Done")
    assert handler._processing_task is None


@pytest.mark.asyncio
async def test_processing_waits_for_delay_and_remaining_messages_cancel_on_failure(db_session, monkeypatch):
    from unittest.mock import AsyncMock

    real_sleep = asyncio.sleep
    delay_elapsed = asyncio.Event()

    async def wait_delay(seconds):
        assert seconds == 3
        await delay_elapsed.wait()

    monkeypatch.setattr("app.routes.wa_webhook.asyncio.sleep", wait_delay)
    handler = WABotHandler(db_session)
    handler.progress_sender = AsyncMock(return_value={"success": True})
    await handler._schedule_processing_items([{"text": "Processing"}], 3)
    await real_sleep(0)
    handler.progress_sender.assert_not_awaited()
    delay_elapsed.set()
    await real_sleep(0)
    handler.progress_sender.assert_awaited_once_with({"text": "Processing"})
    await handler._cancel_processing()

    handler._processing_suppressed = False
    delay_elapsed.clear()
    await handler._schedule_processing_items([{"text": "Pending processing"}], 3)
    await real_sleep(0)
    await handler._send_action_stage(_stage("Fallback"), "camera", "sender")
    delay_elapsed.set()
    await real_sleep(0)
    assert [call.args[0]["text"] for call in handler.progress_sender.call_args_list] == ["Processing", "Fallback"]


@pytest.mark.asyncio
async def test_queued_processing_is_omitted_when_fallback_is_ready(db_session):
    handler = WABotHandler(db_session)
    await handler._schedule_processing_items([{"text": "Processing"}], 0)
    await handler._send_action_stage(_stage("Fallback"), "camera", "sender")
    assert handler.outbound_items == [{"text": "Fallback"}]


def test_video_response_settings_round_trip_and_default_to_text(db_session):
    command = {
        "id": "custom_video", "name": "Video", "trigger": "/video", "action": "api_flow",
        "response": "Video caption", "response_type": "video",
        "response_video_source": "{{steps.data.body.video_url}}",
        "flow": [{"name": "data", "path": "/api/videos/record-and-wait", "method": "POST"}],
    }
    save_command_settings(db_session, [command])
    saved = get_command_settings(db_session)[0]
    assert saved["response_type"] == "video"
    assert saved["response_video_source"] == command["response_video_source"]
    command.pop("response_type")
    save_command_settings(db_session, [command])
    assert get_command_settings(db_session)[0]["response_type"] == "text"
    for update in (
        {"response_video_source": ""}, {"response_video_source": "{{steps.missing.body.video_id}}"},
        {"response_type": "unknown"}, {"response_video_source": "file:///private/video.mp4"},
    ):
        with pytest.raises(ValueError):
            normalize_response_media({**command, "response_type": "video", **update})


def test_every_command_action_can_save_an_image_response(db_session):
    commands = [{
        "id": f"custom_image_{index}", "name": f"Image {action}", "trigger": f"/image{index}",
        "action": action, "response": "", "response_type": "image",
        "response_image_source": "https://example.com/image.jpg",
        "flow": [{"name": "data", "path": "/health"}] if action == "api_flow" else [],
    } for index, action in enumerate(CUSTOM_ACTION_ROLES)]
    save_command_settings(db_session, commands)
    saved = get_command_settings(db_session)
    assert {command["action"] for command in saved} == set(CUSTOM_ACTION_ROLES)
    assert all(command["response_type"] == "image" for command in saved)
    assert all(command["response_image_source"] == "https://example.com/image.jpg" for command in saved)
    with pytest.raises(ValueError):
        normalize_response_media({"action": "text", "response_type": "image"})


@pytest.mark.asyncio
async def test_native_text_command_returns_configured_image_with_caption(db_session):
    save_command_settings(db_session, [{
        "id": "custom_native_image", "name": "Image", "trigger": "/image", "action": "text",
        "response": "Image for {{argument}}", "response_type": "image",
        "response_image_source": "https://example.com/{{argument}}.jpg",
    }])
    handler = WABotHandler(db_session)
    response = await handler.handle("628123456789", "/image gate")
    assert response == ""
    assert not handler.action_failed
    assert handler.outbound_items == [{
        "image_url": "https://example.com/gate.jpg", "text": "Image for gate",
    }]


@pytest.mark.asyncio
async def test_api_failure_variables_render_in_fallback_without_success_media(db_session, monkeypatch):
    from unittest.mock import AsyncMock
    from app.utils.wa_bot_workflow import APIFlowError

    save_command_settings(db_session, [{
        "id": "custom_record_error", "name": "Record", "trigger": "/record", "action": "api_flow",
        "response_type": "video", "response": "Caption",
        "response_video_source": "{{steps.data.body.video_id}}",
        "flow": [{"name": "data", "path": "/api/videos/record-and-wait", "method": "POST"}],
        "failure_messages": _stage("HTTP {{steps.data.status_code}} / {{steps.data.body.status}}: {{steps.data.body.detail}}"),
    }])
    context = {"steps": {"data": {
        "status_code": 404, "body": {"status": "error", "detail": "Camera not found"},
    }}}
    monkeypatch.setattr("app.routes.wa_webhook.run_api_flow", AsyncMock(side_effect=APIFlowError("HTTP 404", context)))
    handler = WABotHandler(db_session)
    assert await handler.handle("628123456789", "/record missing") == ""
    assert handler.action_failed
    assert handler.outbound_items == [{"text": "HTTP 404 / error: Camera not found"}]


@pytest.mark.asyncio
async def test_processing_uses_inputs_before_recording_api_runs(db_session, monkeypatch):
    save_command_settings(db_session, [{
        "id": "custom_record_inputs", "name": "Record", "trigger": "/record", "action": "api_flow",
        "required_params": ["hostname", "duration"], "response": "{{steps.data.body.message}}",
        "flow": [{"name": "data", "path": "/api/videos/record-and-wait", "method": "POST",
                  "body": {"hostname": "{{params.hostname}}", "duration": "{{duration}}"}}],
        "processing_messages": _stage("Recording {{params.hostname}} for {{duration}}s"),
    }])
    events = []

    async def send(item):
        events.append(item["text"])
        return {"success": True}

    async def run(**kwargs):
        assert kwargs["parameters"] == {"hostname": "CCTV-GATE-01", "duration": "30"}
        events.append("API executed")
        return {"params": kwargs["parameters"], "steps": {"data": {"body": {"message": "Recorded"}}}}

    monkeypatch.setattr("app.routes.wa_webhook.run_api_flow", run)
    handler = WABotHandler(db_session)
    handler.progress_sender = send
    assert await handler.handle("628123456789", "/record CCTV-GATE-01 30") == "Recorded"
    assert events == ["Recording CCTV-GATE-01 for 30s", "API executed"]


@pytest.mark.asyncio
async def test_image_response_uses_api_result_and_reports_invalid_source(db_session, monkeypatch):
    command = {
        "id": "custom_api_image", "name": "Image", "trigger": "/image", "action": "api_flow",
        "response": "API image", "response_type": "image",
        "response_image_source": "{{steps.data.body.image_url}}",
        "flow": [{"name": "data", "path": "/image-result"}],
        "failure_messages": _stage("Image unavailable: {{error}}"),
    }
    save_command_settings(db_session, [command])
    from unittest.mock import AsyncMock
    runner = AsyncMock(return_value={"steps": {"data": {"body": {"image_url": "https://example.com/image.jpg"}}}})
    monkeypatch.setattr("app.routes.wa_webhook.run_api_flow", runner)
    handler = WABotHandler(db_session)
    assert await handler.handle("628123456789", "/image") == ""
    assert handler.outbound_items == [{"image_url": "https://example.com/image.jpg", "text": "API image"}]
    runner.return_value = {"steps": {"data": {"body": {"image_url": "file:///private/image.jpg"}}}}
    handler = WABotHandler(db_session)
    assert await handler.handle("628123456789", "/image") == ""
    assert handler.action_failed
    assert all("image_url" not in item for item in handler.outbound_items)
    assert handler.outbound_items[0]["text"].startswith("Image unavailable:")


@pytest.mark.asyncio
async def test_selected_image_replaces_snapshot_success_and_keeps_processing(db_session, monkeypatch):
    handler = WABotHandler(db_session)

    async def run_command(sender, message, is_group):
        handler._response_command = {
            "action": "snap", "response_type": "image",
            "response_image_source": "https://example.com/selected.jpg",
        }
        handler._sender = sender
        handler._command_argument = "gate"
        handler.media_path = "native.jpg"
        handler.media_caption = "Snapshot caption"
        handler.outbound_items = [
            {"text": "Processing"}, {"image_path": "native.jpg", "text": "Snapshot caption"},
        ]
        return ""

    monkeypatch.setattr(handler, "_handle_command", run_command)
    assert await handler.handle("628123456789", "/snap gate") == ""
    assert handler.outbound_items == [
        {"text": "Processing"},
        {"image_url": "https://example.com/selected.jpg", "text": "Snapshot caption"},
    ]
    assert handler.media_path is None


@pytest.mark.asyncio
async def test_protected_image_response_respects_whitelist_group(db_session, monkeypatch, tmp_path):
    from app.models.camera import Camera
    from app.models.camera_group import CameraGroup
    from app.models.snapshot import Snapshot
    from app.models.whitelist import WhatsappWhitelist

    group = CameraGroup(name="Image source group")
    other_group = CameraGroup(name="Other image group")
    camera = Camera(hostname="image-source", ip="192.168.1.5", groups=[group])
    db_session.add_all([camera, other_group])
    db_session.flush()
    entry = WhatsappWhitelist(phone_number="628123456789", group_id=group.id)
    snapshot = Snapshot(camera_id=camera.id, camera_name=camera.hostname,
                        camera_ip=camera.ip, file_path="image.jpg")
    db_session.add_all([entry, snapshot])
    db_session.flush()
    (tmp_path / "image.jpg").write_bytes(b"test image")
    monkeypatch.setattr("app.routes.wa_webhook.Path", lambda value: tmp_path)
    monkeypatch.setattr("app.routes.wa_webhook.log_audit", lambda **kwargs: None)
    handler = WABotHandler(db_session)
    handler._sender = entry.phone_number
    handler._command_argument = ""
    command = {"action": "text", "response_image_source": f"/api/snapshots/secure/{snapshot.id}"}
    await handler._attach_response_image(command, "Caption")
    assert handler.outbound_items == [{"image_path": str(tmp_path / "image.jpg"), "text": "Caption"}]
    handler.outbound_items.clear()
    entry.group_id = other_group.id
    db_session.flush()
    with pytest.raises(ValueError, match="Access denied"):
        await handler._attach_response_image(command, "Caption")
    assert handler.outbound_items == []


def test_stage_messages_can_be_saved_for_every_system_action(db_session):
    commands = []
    for index, action in enumerate(CUSTOM_ACTION_ROLES):
        commands.append({
            "id": f"custom_stage_{index}",
            "name": f"Stage {action}",
            "trigger": f"/stage{index}",
            "action": action,
            "response": "Action completed",
            "flow": [{"name": "result", "path": "/health", "params": {}}] if action == "api_flow" else [],
            "image_flow": [{"name": "image", "path": "/health", "params": {}}] if action == "snap" else [],
            "processing_messages": _stage(f"working {action}"),
            "failure_messages": _stage(f"failed {action}: {{{{error}}}}"),
        })

    save_command_settings(db_session, commands)
    saved = {item["action"]: item for item in get_command_settings(db_session) if item["id"].startswith("custom_")}

    assert set(saved) == set(CUSTOM_ACTION_ROLES)
    for action, command in saved.items():
        assert command["processing_messages"][0]["text"] == f"working {action}"
        assert command["failure_messages"][0]["text"] == f"failed {action}: {{{{error}}}}"
        if action == "api_flow":
            assert command["flow"][0]["name"] == "result"
        elif action == "snap":
            assert command["image_flow"][0]["name"] == "image"


@pytest.mark.asyncio
async def test_handler_sends_processing_and_configured_fallback_on_action_failure(db_session):
    save_command_settings(db_session, [{
        "id": "custom_probe",
        "name": "Probe",
        "trigger": "/probe",
        "action": "ping",
        "response": "",
        "processing_messages": _stage("Checking {{argument}} for {{sender}}"),
        "failure_messages": _stage("Unavailable: {{error}}"),
    }])
    handler = WABotHandler(db_session)
    sent: list[dict[str, str]] = []

    async def sender(item: dict[str, str]) -> dict[str, bool]:
        sent.append(item)
        return {"success": True}

    async def fail_action(action: str, sender_number: str, argument: str, command: dict) -> str:
        handler.action_failed = True
        return "Ping failed for host: offline"

    handler.progress_sender = sender
    handler._handle_n8n_action = fail_action

    response = await handler.handle("628123456789", "/probe host-a")

    assert response == ""
    assert [item["text"] for item in sent] == [
        "Checking host-a for 628123456789",
        "Unavailable: Ping failed for host: offline",
    ]


@pytest.mark.asyncio
async def test_handler_system_action_variables_are_available_without_api_flow(db_session, monkeypatch):
    save_command_settings(db_session, [{
        "id": "custom_probe",
        "name": "Probe",
        "trigger": "/probe",
        "action": "ping",
        "response": "Target: {{steps.ping.body.target}}\n{{#each steps.ping.body.replies}}{{@index1}}. {{this}}\n{{/each}}",
        "processing_messages": _stage("Checking {{argument}}"),
        "failure_messages": _stage("Unavailable"),
    }])
    handler = WABotHandler(db_session)
    sent: list[dict[str, str]] = []
    monkeypatch.setitem(sys.modules, "ping3", SimpleNamespace(ping=lambda target, timeout: 0.012))

    async def sender(item: dict[str, str]) -> dict[str, bool]:
        sent.append(item)
        return {"success": True}

    handler.progress_sender = sender

    response = await handler.handle("628123456789", "/probe host-a")

    assert response.startswith("Target: host-a\n1. Reply from host-a: time=12.00ms")
    assert "4. Reply from host-a: time=12.00ms" in response
    assert handler.private_response is False
    assert [item["text"] for item in sent] == ["Checking host-a"]


@pytest.mark.asyncio
async def test_bot_status_action_formats_gateway_health_and_response_time(db_session, monkeypatch):
    save_command_settings(db_session, [{
        "id": "custom_bot_status",
        "name": "Bot Status",
        "trigger": "/botstatus",
        "action": "bot_status",
        "response": "{{status_icon}} Bot Status: {{status}}\n🕒 Response Time: {{response_time}}",
    }])
    monkeypatch.setattr(WAGatewayService, "check_connection", lambda self: {
        "connected": True,
        "logged_in": True,
        "response_time_ms": 120,
        "error": None,
    })

    response = await WABotHandler(db_session).handle("628123456789", "/botstatus")

    assert response == "✅ Bot Status: Online\n🕒 Response Time: 120 ms"


def test_gateway_status_check_includes_request_round_trip_time(db_session, monkeypatch):
    service = WAGatewayService(db_session)
    service.config._cache.update({"gowa_enabled": "1", "gowa_base_url": "http://gateway.test"})

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"results": {"connected": True, "logged_in": True}}

    monkeypatch.setattr(service.session, "get", lambda url, timeout: FakeResponse())

    result = service.check_connection()

    assert result["connected"] is True
    assert isinstance(result["response_time_ms"], int)
    assert result["response_time_ms"] >= 0
