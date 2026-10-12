"""Isolated schedule tests; no full SQLite schema, gateway, or scheduler startup."""

from __future__ import annotations

import json

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.jobs import wa_command_schedule as jobs
from app.routes import wa_webhook
from app.utils import wa_executor, wa_gateway
from app.utils.cron_description import description_expression
from app.utils.wa_bot_commands import (
    command_schedule_cron,
    get_command_settings,
    normalize_command_schedule,
    save_command_settings,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Override the shared schema fixture: these tests use mock sessions only."""
    return


def command(**schedule_overrides):
    """Return a command with a valid daily schedule."""
    return {
        "id": "custom_report",
        "name": "Report",
        "trigger": "/report",
        "enabled": True,
        "action": "text",
        "response": "Report ready",
        "required_params": [],
        "schedule": {
            "enabled": True,
            "mode": "daily",
            "time": "00:05",
            "cron": "",
            "recipient": "628123456789",
            "argument": "",
            **schedule_overrides,
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        {"time": "24:00"},
        {"time": "8:00"},
        {"recipient": "123"},
        {"recipient": "123@g.us"},
        {"mode": "once"},
        {"enabled": "true"},
        {"mode": "cron", "cron": "0 8 * *"},
        {"argument": "a" * 501},
    ],
)
def test_reject_invalid_schedule(change):
    with pytest.raises(ValueError, match=r".+"):
        normalize_command_schedule(command(**change)["schedule"], [])


def test_required_arguments_and_legacy_defaults():
    assert normalize_command_schedule(None, ["camera"])["enabled"] is False
    with pytest.raises(ValueError, match="all required parameters"):
        normalize_command_schedule(command()["schedule"], ["camera"])
    valid = normalize_command_schedule(
        command(argument="CAM-01 30")["schedule"], ["camera", "duration"]
    )
    assert valid["argument"] == "CAM-01 30"


@pytest.mark.parametrize("zone", ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura"])
def test_daily_schedule_uses_configured_clock(zone):
    timezone = ZoneInfo(zone)
    trigger = CronTrigger.from_crontab(
        command_schedule_cron(command()["schedule"]), timezone=timezone
    )
    next_run = trigger.get_next_fire_time(None, datetime(2026, 10, 12, 23, 59, tzinfo=timezone))
    assert next_run == datetime(2026, 10, 13, 0, 5, tzinfo=timezone)


def test_reconcile_preserves_jobs_and_removes_disabled_commands(monkeypatch):
    commands = [command()]
    monkeypatch.setattr(jobs, "SessionLocal", lambda: nullcontext(Mock()))
    monkeypatch.setattr(jobs, "get_command_settings", lambda _db: commands)
    scheduler = BackgroundScheduler(timezone="Asia/Makassar")
    scheduler.add_job(lambda: None, "interval", hours=1, id="other_job")
    jobs.sync_command_jobs(scheduler)
    job = scheduler.get_job("wa_command:custom_report")
    reschedule = Mock(wraps=job.reschedule)
    monkeypatch.setattr(type(job), "reschedule", lambda _self, **kwargs: reschedule(**kwargs))
    jobs.sync_command_jobs(scheduler)
    reschedule.assert_not_called()
    commands[0]["schedule"]["time"] = "09:30"
    jobs.sync_command_jobs(scheduler)
    reschedule.assert_called_once()
    commands[0]["enabled"] = False
    jobs.sync_command_jobs(scheduler)
    assert scheduler.get_job(job.id) is None
    assert scheduler.get_job("other_job") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "image", "video", "failed"])
async def test_execute_logs_gateway_outcomes(monkeypatch, kind):

    item = {"text": "Result"}
    if kind in {"image", "video"}:
        item[f"{kind}_path"] = "mock-file"
    bot = SimpleNamespace(
        handle=AsyncMock(return_value="Result"),
        outbound_items=[] if kind == "text" else [item],
        media_items=[],
        media_path=None,
        action_failed=False,
    )
    monkeypatch.setattr(wa_webhook, "WABotHandler", lambda _db: bot)
    monkeypatch.setattr(wa_webhook, "_whitelisted_sender", lambda _db, _sender: object())
    monkeypatch.setattr(wa_webhook, "_command_access_error", lambda _entry, _cmd: "")
    record = Mock()
    monkeypatch.setattr(wa_webhook, "_record_wa_message", record)
    monkeypatch.setattr(
        wa_gateway,
        "WAGatewayService",
        lambda _db: SimpleNamespace(config=SimpleNamespace(enabled=True)),
    )
    send = AsyncMock(return_value={"success": kind != "failed", "error": ""})
    monkeypatch.setattr(wa_executor, "run_gateway_blocking", send)
    db = Mock()
    if kind == "failed":
        with pytest.raises(RuntimeError):
            await jobs.execute_scheduled_command(db, command())
    else:
        assert await jobs.execute_scheduled_command(db, command()) == 1
    assert send.await_args.args[2] == "628123456789"
    assert record.call_args.args[3] == ("failed" if kind == "failed" else "accepted")
    assert bot._test_command["id"] == "custom_report"


@pytest.mark.asyncio
async def test_execution_rechecks_revoked_access(monkeypatch):

    monkeypatch.setattr(wa_webhook, "_whitelisted_sender", lambda _db, _sender: None)
    with pytest.raises(ValueError, match="whitelist"):
        await jobs.execute_scheduled_command(Mock(), command())


def test_settings_round_trip_with_mock_database():
    """Verify schedules persist in Configuration without a schema migration."""

    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    saved = save_command_settings(db, [command(argument="CAM-01")])
    row = db.add.call_args.args[0]
    db.query.return_value.filter.return_value.first.return_value = row
    loaded = get_command_settings(db)
    assert loaded[0]["schedule"] == saved[0]["schedule"]
    assert loaded[0]["schedule"]["argument"] == "CAM-01"


@pytest.mark.asyncio
async def test_job_management_includes_command_before_worker_reload(monkeypatch):
    from app.routes import jobs as routes  # noqa: PLC0415

    monkeypatch.setattr(
        routes, "scheduled_commands", lambda _db: {"wa_command:custom_report": command()}
    )
    monkeypatch.setattr(routes, "get_jobs_from_db", lambda: [])
    monkeypatch.setattr(
        routes,
        "get_job_schedule",
        lambda _job_id: {
            "cron_expression": "0 8 * * *",
            "interval": None,
            "interval_unit": None,
        },
    )
    monkeypatch.setattr(routes, "get_current_timezone", lambda _db: "Asia/Makassar")
    monkeypatch.setattr(routes.JobExecutionLog, "get_job_stats", lambda _db, **_kwargs: {})
    db = Mock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    response = await routes.list_jobs(db=db, current_admin=Mock())

    data = json.loads(response.body)
    job = next(item for item in data["jobs"] if item["id"] == "wa_command:custom_report")
    assert job["cron_expression"] == "5 0 * * *"
    assert job["registered"] is False
    assert job["schedule_editable"] is False
    assert data["timezone"] == "Asia/Makassar"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("*/5 * * * *", "*/5 * * * *"),
        ("0 8 * * 0", "0 8 * * MON"),
        ("0 8 * * 6", "0 8 * * SUN"),
        ("0 8 * * 0-4", "0 8 * * MON,TUE,WED,THU,FRI"),
        ("0 8 * * mon-fri", "0 8 * * MON,TUE,WED,THU,FRI"),
        ("0 8 * * */2", "0 8 * * MON,WED,FRI,SUN"),
        ("0 8 last jan 1,3", "0 8 L JAN TUE,THU"),
        ("0 8 1 * 0", "0 8 1 * MON"),
    ],
)
def test_cron_description_preserves_scheduler_semantics(expression, expected):
    assert description_expression(expression) == expected


@pytest.mark.parametrize(
    "expression", ["* * * *", "* * * * * *", "*/0 * * * *", "0 24 * * *", "0 8 * * 7", "0 8 ? * *"]
)
def test_cron_description_rejects_syntax_not_supported_by_scheduler(expression):
    with pytest.raises(ValueError, match=r".+"):
        description_expression(expression)


def test_cron_preview_uses_configured_timezone_and_validation(monkeypatch):
    from fastapi import HTTPException  # noqa: PLC0415

    from app.routes import jobs as routes  # noqa: PLC0415

    monkeypatch.setattr(routes, "get_current_timezone", lambda _db: "Asia/Jayapura")
    assert routes.cron_preview("0 8 * * 0", db=Mock(), current_admin=Mock()) == {
        "expression": "0 8 * * MON",
        "timezone": "Asia/Jayapura",
    }
    with pytest.raises(HTTPException) as error:
        routes.cron_preview("*/0 * * * *", db=Mock(), current_admin=Mock())
    assert error.value.status_code == 422
