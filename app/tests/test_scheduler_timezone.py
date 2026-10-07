"""Regression coverage for configured scheduler and report timezones."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.jobs import scheduler as jobs
from app.core import job_schedules


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """These isolated tests mock database access and need no schema creation."""


@pytest.mark.parametrize("use_cron", [False, True])
def test_start_scheduler_applies_timezone_to_every_job(monkeypatch, use_cron: bool) -> None:
    scheduler = BackgroundScheduler(timezone=pytz.UTC)
    monkeypatch.setattr(jobs, "scheduler", scheduler)
    monkeypatch.setattr(jobs, "engine", MagicMock())
    monkeypatch.setattr(jobs, "last_config", {})
    monkeypatch.setattr(jobs, "get_scheduler_timezone", lambda: pytz.timezone("Asia/Makassar"))
    monkeypatch.setattr(
        job_schedules,
        "get_config",
        lambda key, default: "0 8 * * *" if use_cron and key.endswith("_cron") else default,
    )
    monkeypatch.setattr(scheduler, "start", MagicMock())
    monkeypatch.setattr(jobs, "get_config", job_schedules.get_config)
    jobs.start_scheduler()
    registered_jobs = scheduler.get_jobs()
    assert len(registered_jobs) == 14
    assert all(str(job.trigger.timezone) == "Asia/Makassar" for job in registered_jobs)
    assert jobs.last_config["timezone"] == "Asia/Makassar"
    now = datetime(2026, 10, 6, 18, tzinfo=timezone.utc)
    next_run = scheduler.get_job("retention_policy").trigger.get_next_fire_time(None, now)
    expected = (
        datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
        if use_cron
        else datetime(2026, 10, 6, 19, tzinfo=timezone.utc)
    )
    assert next_run.astimezone(timezone.utc) == expected


@pytest.mark.parametrize("timezone_name", ["Asia/Makassar", "Asia/Jakarta", "UTC"])
def test_cron_runs_at_configured_local_hour(monkeypatch, timezone_name: str) -> None:
    scheduler = BackgroundScheduler(timezone=pytz.timezone(timezone_name))
    monkeypatch.setattr(jobs, "scheduler", scheduler)
    trigger = jobs.create_trigger("0 8 * * *", IntervalTrigger(hours=1))
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    next_run = trigger.get_next_fire_time(None, now)
    assert str(trigger.timezone) == timezone_name
    assert next_run.hour == 8
    assert (
        next_run.astimezone(timezone.utc).hour
        == {"Asia/Makassar": 0, "Asia/Jakarta": 1, "UTC": 8}[timezone_name]
    )


def test_timezone_reload_updates_all_jobs_and_preserves_intervals(monkeypatch) -> None:
    scheduler = BackgroundScheduler(timezone=pytz.UTC)
    scheduler.add_job(lambda: None, CronTrigger(hour=8, timezone=pytz.UTC), id="daily")
    scheduler.add_job(lambda: None, CronTrigger(hour=3, timezone=pytz.UTC), id="retention")
    scheduler.add_job(lambda: None, IntervalTrigger(minutes=15, timezone=pytz.UTC), id="interval")
    scheduler.start(paused=True)
    try:
        original_interval = scheduler.get_job("interval").trigger
        monkeypatch.setattr(jobs, "scheduler", scheduler)
        monkeypatch.setattr(jobs, "last_config", {"timezone": "UTC"})
        monkeypatch.setattr(jobs, "get_scheduler_timezone", lambda: pytz.timezone("Asia/Makassar"))
        jobs.update_scheduler_timezone()
        assert str(scheduler.timezone) == "Asia/Makassar"
        for job in scheduler.get_jobs():
            assert str(job.trigger.timezone) == "Asia/Makassar"
        interval = scheduler.get_job("interval").trigger
        assert interval.interval == original_interval.interval
        assert interval.start_date == original_interval.start_date
        now = datetime(2026, 10, 6, 23, tzinfo=timezone.utc)
        next_run = scheduler.get_job("daily").trigger.get_next_fire_time(None, now)
        assert next_run == datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
        # Unchanged config must not reset next run times.
        next_runs = {job.id: job.next_run_time for job in scheduler.get_jobs()}
        jobs.update_scheduler_timezone()
        assert next_runs == {job.id: job.next_run_time for job in scheduler.get_jobs()}
    finally:
        scheduler.shutdown()


def test_daily_report_time_updates_both_reports(monkeypatch) -> None:
    scheduler = MagicMock(timezone=pytz.timezone("Asia/Jakarta"))
    monkeypatch.setattr(jobs, "scheduler", scheduler)
    jobs.handle_wa_daily_report_time(scheduler, 9, 30)
    assert [call.args[0] for call in scheduler.reschedule_job.call_args_list] == [
        "wa_daily_report",
        "record_check_daily_report",
    ]
    for call in scheduler.reschedule_job.call_args_list:
        trigger = call.kwargs["trigger"]
        next_run = trigger.get_next_fire_time(None, datetime(2026, 10, 7, tzinfo=timezone.utc))
        assert next_run.hour == 9
        assert next_run.minute == 30
        assert str(trigger.timezone) == "Asia/Jakarta"


@pytest.mark.parametrize("configured_timezone", ["Asia/Makassar", "invalid/timezone", None])
def test_scheduler_timezone_reads_config_and_closes_session(
    monkeypatch, configured_timezone
) -> None:
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = (
        MagicMock(value=configured_timezone) if configured_timezone else None
    )
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    expected = configured_timezone if configured_timezone == "Asia/Makassar" else "UTC"
    assert str(jobs.get_scheduler_timezone()) == expected
    db.close.assert_called_once()


@pytest.mark.parametrize(
    ("timezone_name", "date_header"),
    [
        ("Asia/Makassar", "2026-10-07 04:30 WITA"),
        ("Asia/Jakarta", "2026-10-07 03:30 WIB"),
        ("Asia/Jayapura", "2026-10-07 05:30 WIT"),
        ("UTC", "2026-10-06 20:30 UTC"),
    ],
)
def test_wa_report_uses_local_date_and_utc_seven_day_cutoff(
    monkeypatch, timezone_name: str, date_header: str
) -> None:
    now = datetime(2026, 10, 6, 20, 30, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    db = MagicMock()
    config_query = MagicMock()
    config_query.filter_by.return_value.first.return_value = MagicMock(value=timezone_name)
    camera = MagicMock(id=1, hostname="Camera 1", ip="192.0.2.1")
    camera_query = MagicMock()
    camera_query.filter.return_value.all.return_value = [camera]
    snapshot_query = MagicMock()
    snapshot_query.filter.return_value = snapshot_query
    snapshot_query.first.return_value = None
    health_query = MagicMock()
    health_query.filter.return_value.all.return_value = []
    db.query.side_effect = lambda model: {
        jobs.Camera: camera_query,
        jobs.SnapshotLog: snapshot_query,
        jobs.CameraHealth: health_query,
    }.get(model, config_query)
    service = MagicMock()
    service.config.default_receiver = "report@g.us"
    service.send_text.return_value = {"success": True}
    monkeypatch.setattr(jobs, "SessionLocal", lambda: db)
    monkeypatch.setattr(jobs, "WAGatewayService", lambda db: service)
    monkeypatch.setattr(jobs, "datetime", FixedDatetime)
    assert jobs.send_wa_camera_no_snapshot_report() == {"records_processed": 1}
    message = service.send_text.call_args.args[1]
    assert f"Date: {date_header}" in message
    assert "last 7 days" in message
    cutoff_filter = snapshot_query.filter.call_args_list[1].args[0]
    assert cutoff_filter.right.value == now - timedelta(days=7)
    assert cutoff_filter.right.value.tzinfo == timezone.utc
    db.close.assert_called_once()
