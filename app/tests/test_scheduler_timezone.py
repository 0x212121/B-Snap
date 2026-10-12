"""Regression coverage for configured scheduler and report timezones."""

from __future__ import annotations

from datetime import datetime, timezone
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
    monkeypatch.setattr(jobs, "sync_command_jobs", lambda scheduler: None)
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
    assert scheduler.get_job("wa_daily_report") is None
    assert scheduler.get_job("record_check_daily_report") is not None
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
