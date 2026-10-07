"""Independent schedule editing, defaults, validation, and scheduler reloads."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app.core import job_schedules as schedules
from app.jobs import scheduler as worker
from app.routes import jobs as routes


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Database access is mocked; these tests do not need application tables."""


@pytest.fixture
def config(monkeypatch) -> dict:
    values = {}

    def get_config(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(schedules, "get_config", get_config)
    monkeypatch.setattr(worker, "get_config", get_config)
    return values


@pytest.mark.parametrize("job_id", list(schedules.JOB_SCHEDULES))
@pytest.mark.asyncio
async def test_every_listed_job_has_an_independent_schedule(job_id: str) -> None:
    assert set(routes.CONFIGURED_JOBS) == set(schedules.JOB_SCHEDULES)
    assert len({definition[0] for definition in schedules.JOB_SCHEDULES.values()}) == 14
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    response = await routes.update_job_schedule(
        job_id, cron_expression="15 9 * * *", db=db, current_admin=MagicMock()
    )
    assert response.status_code == 200
    saved = db.add.call_args.args[0]
    assert saved.key == schedules.JOB_SCHEDULES[job_id][0]
    assert saved.value == "15 9 * * *"
    db.commit.assert_called_once()


@pytest.mark.parametrize("cron", ["61 8 * * *", "0 25 * * *", "0 8 * *", "garbage", "0 8 * 13 *"])
@pytest.mark.asyncio
async def test_invalid_cron_is_rejected_before_save(cron: str) -> None:
    db = MagicMock()
    response = await routes.update_job_schedule(
        "wa_daily_report", cron_expression=cron, db=db, current_admin=MagicMock()
    )
    assert response.status_code == 400
    db.commit.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_job_is_rejected() -> None:
    db = MagicMock()
    response = await routes.update_job_schedule(
        "unknown", cron_expression="0 8 * * *", db=db, current_admin=MagicMock()
    )
    assert response.status_code == 400
    db.commit.assert_not_called()


@pytest.mark.parametrize("job_id", list(schedules.JOB_SCHEDULES))
def test_reload_changes_only_target_job_and_reset_restores_default(
    monkeypatch, config, job_id: str
) -> None:
    scheduler = BackgroundScheduler(timezone="Asia/Makassar")
    settings = {key: schedules.get_job_schedule(key) for key in schedules.JOB_SCHEDULES}
    for key, value in settings.items():
        scheduler.add_job(
            lambda: None, schedules.build_job_trigger(value, scheduler.timezone), id=key
        )
    monkeypatch.setattr(worker, "scheduler", scheduler)
    monkeypatch.setattr(worker, "update_scheduler_timezone", lambda: None)
    monkeypatch.setattr(
        worker,
        "last_config",
        {
            "job_schedules": settings,
            "snapshot_concurrent_workers": 5,
            "snapshot_batch_size": 50,
            "snapshot_batch_delay_seconds": 5,
        },
    )
    original_triggers = {job.id: job.trigger for job in scheduler.get_jobs()}
    key = schedules.JOB_SCHEDULES[job_id][0]
    config[key] = "15 9 * * *"
    worker.update_scheduler_config()
    changed = scheduler.get_job(job_id).trigger
    assert str(changed.timezone) == "Asia/Makassar"
    assert str(changed) == str(
        schedules.build_job_trigger(schedules.get_job_schedule(job_id), scheduler.timezone)
    )
    assert changed is not original_triggers[job_id]
    for job in scheduler.get_jobs():
        if job.id != job_id:
            assert job.trigger is original_triggers[job.id]
    config[key] = ""
    worker.update_scheduler_config()
    assert str(scheduler.get_job(job_id).trigger) == str(original_triggers[job_id])
    # Polling unchanged configuration must preserve trigger and next-run calculations.
    restored = scheduler.get_job(job_id).trigger
    worker.update_scheduler_config()
    assert scheduler.get_job(job_id).trigger is restored


def test_legacy_cleanup_and_report_times_are_preserved(config) -> None:
    config.update(cleanup_cron="0 4 * * *", wa_daily_report_hour=11, wa_daily_report_minute=25)
    assert schedules.get_job_schedule("cleanup_api_logs")["cron_expression"] == "0 4 * * *"
    assert (
        schedules.get_job_schedule("record_check_daily_report")["cron_expression"] == "25 11 * * *"
    )
    config["cleanup_api_logs_cron"] = "0 5 * * *"
    assert schedules.get_job_schedule("cleanup_api_logs")["cron_expression"] == "0 5 * * *"
    assert schedules.get_job_schedule("cleanup_command_logs")["cron_expression"] == "0 4 * * *"


@pytest.mark.asyncio
async def test_clear_schedule_saves_empty_override() -> None:
    db = MagicMock()
    existing = db.query.return_value.filter.return_value.first.return_value
    response = await routes.update_job_schedule(
        "retention_policy", cron_expression=" ", db=db, current_admin=MagicMock()
    )
    assert response.status_code == 200
    assert existing.value == ""
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_job_list_exposes_editing_for_all_jobs(monkeypatch, config) -> None:
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    monkeypatch.setattr(routes, "get_jobs_from_db", lambda: [])
    monkeypatch.setattr(routes.JobExecutionLog, "get_job_stats", lambda *args, **kwargs: {})
    response = await routes.list_jobs(db=db, current_admin=MagicMock())
    result = json.loads(response.body)
    assert len(result["jobs"]) == 14
    assert all(job["schedule_editable"] for job in result["jobs"])
    assert len(result["cron_configs"]) == 14
