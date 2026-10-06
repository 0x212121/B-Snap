"""Sample previews use production template rendering without executing actions."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.routes import wa_webhook


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Rendering sample data does not require a database."""


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda db: "Asia/Makassar")
    monkeypatch.setattr(wa_webhook, "WABotHandler", Mock(side_effect=AssertionError("Action executed")))
    monkeypatch.setattr(wa_webhook, "run_api_flow", Mock(side_effect=AssertionError("API called")))
    return lambda **values: wa_webhook.preview_wa_bot_command(
        wa_webhook.WABotPreviewRequest(**values), db=Mock(), current_admin=SimpleNamespace(id=1)
    )


@pytest.mark.parametrize("action", ["snap", "edit", "whitelist_add", "whitelist_remove", "api_flow"])
def test_preview_renders_without_executing_mutating_actions(preview, action):
    data = preview(command={"action": action, "response": "Hi {{sender}}: {{argument}}"},
                   argument="CAM-01", sender="628111111111")
    assert data["messages"][0]["text"] == "Hi 628111111111: CAM-01"


def test_preview_array_mapping_datetime_named_parameters_and_stage_order(preview):
    data = preview(
        command={"action": "api_flow", "required_params": ["camera"],
                 "response": "{{#each steps.data.body}}\n{{@index1}} {{this.name}} {{this.time|datetime}}{{/each}}",
                 "processing_messages": [{"text": "Working on {{params.camera}}"}],
                 "processing_delay_seconds": 3, "response_type": "video",
                 "response_video_source": "{{steps.record.body.video_id}}"},
        argument="CAM-01", steps={"data": {"body": [{"name": "North", "time": "2026-10-06T00:00:00Z"}]},
                                  "record": {"body": {"video_id": "video-123"}}},
    )
    assert [item["stage"] for item in data["messages"]] == ["processing", "success"]
    assert data["messages"][0]["text"] == "Working on CAM-01"
    assert "1 North" in data["messages"][1]["text"]
    assert "08:00" in data["messages"][1]["text"]
    assert data["messages"][1]["source"] == "video-123"


def test_preview_failure_and_media_are_metadata_only(preview):
    data = preview(command={"failure_messages": [{"text": "Failed: {{error}}", "image_source_type": "last_snapshot"}]},
                   steps={"error": {"body": {"message": "Camera offline"}}}, outcome="failure")
    assert data["messages"] == [{"stage": "fallback", "text": "Failed: Camera offline",
                                 "media_type": "image", "source": "Latest saved snapshot"}]


def test_preview_rejects_missing_required_arguments(preview):
    with pytest.raises(HTTPException) as error:
        preview(command={"required_params": ["camera"]})
    assert error.value.status_code == 422
