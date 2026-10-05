"""Tests for configurable WhatsApp action processing and fallback messages."""
from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import event

from app.utils.wa_bot_commands import CUSTOM_ACTION_ROLES, get_command_settings, save_command_settings
from app.routes.wa_webhook import WABotHandler
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
            "image_flow": [],
            "processing_messages": _stage(f"working {action}"),
            "failure_messages": _stage(f"failed {action}: {{{{error}}}}"),
        })

    save_command_settings(db_session, commands)
    saved = {item["action"]: item for item in get_command_settings(db_session) if item["id"].startswith("custom_")}

    assert set(saved) == set(CUSTOM_ACTION_ROLES)
    for action, command in saved.items():
        assert command["processing_messages"][0]["text"] == f"working {action}"
        assert command["failure_messages"][0]["text"] == f"failed {action}: {{{{error}}}}"


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
async def test_handler_sends_processing_without_fallback_when_action_succeeds(db_session):
    save_command_settings(db_session, [{
        "id": "custom_probe",
        "name": "Probe",
        "trigger": "/probe",
        "action": "ping",
        "response": "",
        "processing_messages": _stage("Checking"),
        "failure_messages": _stage("Unavailable"),
    }])
    handler = WABotHandler(db_session)
    sent: list[dict[str, str]] = []

    async def sender(item: dict[str, str]) -> dict[str, bool]:
        sent.append(item)
        return {"success": True}

    async def succeed(action: str, sender_number: str, argument: str, command: dict) -> str:
        return "Ping succeeded"

    handler.progress_sender = sender
    handler._handle_n8n_action = succeed

    response = await handler.handle("628123456789", "/probe host-a")

    assert response == "Ping succeeded"
    assert [item["text"] for item in sent] == ["Checking"]
