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
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.jobs import wa_command_schedule as jobs
from app.routes import jobs as job_routes, wa_webhook
from app.utils import wa_executor, wa_gateway
from app.utils.cron_description import description_expression
from app.utils.wa_bot_commands import (
    command_schedule_cron,
    get_command_settings,
    normalize_command_schedule,
    normalize_command_schedules,
    save_command_settings,
    schedule_execution_sender,
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


def test_group_schedule_requires_execution_identity_and_rejects_private_commands():
    group = "120363123456789@g.us"
    with pytest.raises(ValueError, match="execution phone"):
        normalize_command_schedule(command(recipient=group)["schedule"], [])
    schedule = normalize_command_schedule(
        command(recipient=group, execution_phone="+628123456789")["schedule"], []
    )
    assert schedule["recipient"] == group
    assert schedule_execution_sender(command(), schedule) == "628123456789"
    with pytest.raises(ValueError, match="Private-only"):
        schedule_execution_sender({"action": "text", "chat_scope": "private"}, schedule)


@pytest.mark.asyncio
async def test_group_execution_uses_whitelist_identity_but_delivers_to_group(monkeypatch):
    group = "120363123456789@g.us"
    entry = command(recipient=group, execution_phone="628111111111")
    bot = SimpleNamespace(
        handle=AsyncMock(return_value="Result"),
        outbound_items=[],
        media_items=[],
        media_path=None,
        action_failed=False,
    )
    whitelist = Mock(return_value=object())
    monkeypatch.setattr(wa_webhook, "WABotHandler", lambda _db: bot)
    monkeypatch.setattr(wa_webhook, "_whitelisted_sender", whitelist)
    monkeypatch.setattr(wa_webhook, "_command_access_error", lambda _entry, _cmd: "")
    monkeypatch.setattr(wa_webhook, "_record_wa_message", Mock())
    monkeypatch.setattr(
        wa_gateway,
        "WAGatewayService",
        lambda _db: SimpleNamespace(config=SimpleNamespace(enabled=True)),
    )
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(wa_executor, "run_gateway_blocking", send)
    db = Mock()
    assert await jobs.execute_scheduled_command(db, entry) == 1
    whitelist.assert_called_once_with(db, "628111111111")
    bot.handle.assert_awaited_once_with("628111111111", "/report")
    assert send.await_args.args[2] == group
    whitelist.return_value = None
    monkeypatch.setattr(
        wa_webhook,
        "_command_access_error",
        lambda allowed, _cmd: "" if allowed else "Access revoked",
    )
    with pytest.raises(ValueError, match="Access revoked"):
        await jobs.execute_scheduled_command(db, entry)
    send.assert_awaited_once()
    bot.handle.assert_awaited_once()


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
    scheduler.add_job(lambda: None, "interval", hours=1, id="wa_command:/report")
    jobs.sync_command_jobs(scheduler)
    assert scheduler.get_job("wa_command:/report") is None
    job = scheduler.get_job("wa_command:custom_report")
    assert job.args == ("custom_report",)
    reschedule = Mock(wraps=job.reschedule)
    monkeypatch.setattr(type(job), "reschedule", lambda _self, **kwargs: reschedule(**kwargs))
    jobs.sync_command_jobs(scheduler)
    reschedule.assert_not_called()
    commands[0]["trigger"] = "/renamed-report"
    jobs.sync_command_jobs(scheduler)
    assert scheduler.get_job("wa_command:custom_report") is job
    reschedule.assert_not_called()
    commands[0]["schedule"]["time"] = "09:30"
    jobs.sync_command_jobs(scheduler)
    reschedule.assert_called_once()
    commands[0]["enabled"] = False
    jobs.sync_command_jobs(scheduler)
    assert scheduler.get_job(job.id) is None
    assert scheduler.get_job("other_job") is not None


def test_scheduled_command_executes_and_logs_using_internal_id(monkeypatch):
    db = Mock()
    log = Mock()
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(jobs, "get_command_settings", lambda _db: [command()])
    monkeypatch.setattr(jobs, "execute_scheduled_command", execute)
    start = Mock(return_value=log)
    monkeypatch.setattr(jobs.JobExecutionLog, "start_execution", start)

    jobs.run_scheduled_command("custom_report")

    start.assert_called_once_with(db, "wa_command:custom_report", "WhatsApp: Report")
    execute.assert_awaited_once()
    assert execute.await_args.args[1]["trigger"] == "/report"
    log.complete.assert_called_once_with(db, "success", records=1)


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


def multiple_command():
    entry = command()
    entry["required_params"] = ["camera"]
    entry["schedules"] = [
        {"id": "morning", **command(argument="CAM-01")["schedule"]},
        {
            "id": "evening",
            **command(argument="CAM-02", recipient="628999999999", mode="cron", cron="30 18 * * *")[
                "schedule"
            ],
        },
    ]
    return entry


def test_multiple_schedules_round_trip_and_legacy_identity():
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    saved = save_command_settings(db, [multiple_command()])
    row = db.add.call_args.args[0]
    db.query.return_value.filter.return_value.first.return_value = row
    loaded = get_command_settings(db)[0]
    assert loaded["schedules"] == saved[0]["schedules"]
    assert [schedule["argument"] for schedule in loaded["schedules"]] == ["CAM-01", "CAM-02"]
    legacy = normalize_command_schedules(command(), [])
    assert legacy[0]["id"] == "legacy"
    assert jobs.command_job_id("custom_report", legacy[0]["id"]) == "wa_command:custom_report"


@pytest.mark.parametrize(
    "kind", ["duplicate", "unsafe", "missing_arguments", "too_many", "too_long"]
)
def test_multiple_schedule_validation(kind):
    entry = multiple_command()
    if kind == "duplicate":
        entry["schedules"][1]["id"] = "morning"
    elif kind == "unsafe":
        entry["schedules"][1]["id"] = "/report"
    elif kind == "missing_arguments":
        entry["schedules"][1]["argument"] = ""
    elif kind == "too_long":
        entry["id"] = "a" * 89
    else:
        entry["schedules"] *= 26
    with pytest.raises(ValueError, match=r".+"):
        normalize_command_schedules(entry, entry["required_params"])


def test_multiple_schedule_reload_changes_only_target_and_removes_deleted(monkeypatch):
    entry = multiple_command()
    monkeypatch.setattr(jobs, "SessionLocal", lambda: nullcontext(Mock()))
    monkeypatch.setattr(jobs, "get_command_settings", lambda _db: [entry])
    scheduler = BackgroundScheduler(timezone="Asia/Makassar")
    jobs.sync_command_jobs(scheduler)
    morning = scheduler.get_job("wa_command:custom_report:morning")
    evening = scheduler.get_job("wa_command:custom_report:evening")
    assert morning.args == ("custom_report", "morning")
    assert evening.args == ("custom_report", "evening")
    evening_trigger = evening.trigger
    morning_trigger = morning.trigger
    entry["schedules"][0]["time"] = "09:00"
    jobs.sync_command_jobs(scheduler)
    assert morning.trigger is not morning_trigger
    assert evening.trigger is evening_trigger
    entry["schedules"][0]["argument"] = "CAM-03"
    unchanged_trigger = morning.trigger
    jobs.sync_command_jobs(scheduler)
    assert morning.trigger is unchanged_trigger
    assert "CAM-03" in morning.name
    entry["schedules"].pop()
    jobs.sync_command_jobs(scheduler)
    assert scheduler.get_job(evening.id) is None
    assert scheduler.get_job(morning.id) is morning


@pytest.mark.parametrize("schedule_id", ["morning", "evening"])
def test_execution_selects_schedule_arguments_recipient_and_history(monkeypatch, schedule_id):
    entry = multiple_command()
    db, log = Mock(), Mock()
    execute = AsyncMock(return_value=1)
    start = Mock(return_value=log)
    monkeypatch.setattr(jobs, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(jobs, "get_command_settings", lambda _db: [entry])
    monkeypatch.setattr(jobs, "execute_scheduled_command", execute)
    monkeypatch.setattr(jobs.JobExecutionLog, "start_execution", start)
    jobs.run_scheduled_command(entry["id"], schedule_id)
    selected = execute.await_args.args[1]["schedule"]
    expected = next(item for item in entry["schedules"] if item["id"] == schedule_id)
    assert selected["argument"] == expected["argument"]
    assert selected["recipient"] == expected["recipient"]
    assert start.call_args.args[1] == f"wa_command:custom_report:{schedule_id}"
    log.complete.assert_called_once_with(db, "success", records=1)


@pytest.mark.parametrize("schedule_id", ["morning", "evening"])
def test_schedule_history_url_reaches_its_database_filter(monkeypatch, schedule_id):
    db = Mock()
    recent = Mock(return_value=[])
    monkeypatch.setattr(job_routes.JobExecutionLog, "get_recent_executions", recent)
    monkeypatch.setattr(job_routes, "get_current_timezone", lambda _db: "Asia/Makassar")
    app = FastAPI()
    app.include_router(job_routes.router)
    app.dependency_overrides[job_routes.get_db] = lambda: db
    app.dependency_overrides[job_routes.admin_access_required] = lambda: None
    with TestClient(app) as client:
        response = client.get(
            f"/admin/jobs/api/jobs/wa_command%3Acustom_report%3A{schedule_id}/history?limit=50"
        )
    assert response.status_code == 200
    assert response.json()["job_id"] == f"wa_command:custom_report:{schedule_id}"
    recent.assert_called_once_with(db, job_id=f"wa_command:custom_report:{schedule_id}", limit=50)


@pytest.mark.asyncio
async def test_save_checks_access_for_every_schedule(monkeypatch):
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(
        wa_webhook, "_whitelisted_sender", lambda _db, phone: phone == "628123456789"
    )
    monkeypatch.setattr(
        wa_webhook,
        "_command_access_error",
        lambda allowed, _command: "" if allowed else "Recipient not allowed",
    )
    request = SimpleNamespace(json=AsyncMock(return_value={"commands": [multiple_command()]}))
    with pytest.raises(HTTPException, match="Recipient not allowed") as error:
        await wa_webhook.put_wa_bot_commands(request, db, SimpleNamespace(api_tokens=[]))
    assert error.value.status_code == 422
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


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
