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


# The shared test database is SQLite, while a few production models use JSONB.
@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"


# The project-wide SQLite fixture cannot create PostgreSQL-only audit triggers.
event.remove(AuditLog.__table__, "after_create", create_audit_log_triggers)


def _stage(text: str) -> list[dict[str, str]]:
    return [{"text": text, "image_source_type": "none", "image_source": ""}]


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
