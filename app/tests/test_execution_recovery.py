"""Recovery regressions using only SQLite-compatible tables, never the application DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.jobs.execution_recovery import INTERRUPTED_MESSAGE, recover_interrupted_executions
from app.models.health_check_status import HealthCheckStatus
from app.models.job_execution_log import JobExecutionLog
from app.models.record_check import RecordCheckRun


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the global full-schema fixture: JSONB cannot be created in SQLite."""


@pytest.fixture
def recovery_db():
    engine = create_engine("sqlite:///:memory:")
    # These tables support SQLite. Foreign keys are not enforced in this isolated
    # fixture; no cameras, record sources, PostgreSQL schema, or scheduler run.
    for model in (JobExecutionLog, RecordCheckRun, HealthCheckStatus):
        model.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_recovery_preserves_results_and_completed_or_new_runs(recovery_db: Session) -> None:
    startup = datetime(2026, 10, 15, tzinfo=UTC)
    previous = startup - timedelta(hours=2)
    cases = [
        ("running", previous),
        ("success", previous),
        ("fail", previous),
        ("timeout", previous),
        ("partial", previous),
        ("running", startup),
        ("running", startup + timedelta(seconds=1)),
    ]
    for index, (status, started_at) in enumerate(cases, start=1):
        recovery_db.add(
            JobExecutionLog(
                id=index,
                job_id="wa_command:test" if index == 1 else "scheduled_snapshot",
                job_name="Test",
                status=status,
                started_at=started_at,
                records_processed=3,
                metadata_json='{"processed": 3}',
                duration_ms=100 if status != "running" else None,
            )
        )
        recovery_db.add(
            RecordCheckRun(
                id=str(index),
                source_id="test-source",
                status=status,
                started_at=started_at,
                total_folders=5,
                healthy_count=3,
            )
        )
    recovery_db.add_all(
        [
            HealthCheckStatus(id=1, is_running=True, start_time=previous, completed_cameras=3),
            HealthCheckStatus(id=2, is_running=True, start_time=startup),
            HealthCheckStatus(id=3, is_running=False, start_time=previous),
            HealthCheckStatus(id=4, is_running=True, start_time=None),
        ]
    )
    recovery_db.commit()

    assert recover_interrupted_executions(recovery_db, startup) == {
        "jobs": 1,
        "record_checks": 1,
        "health_checks": 2,
    }
    recovery_db.expire_all()
    log = recovery_db.get(JobExecutionLog, 1)
    assert log.status == "fail"
    assert log.ended_at.replace(tzinfo=UTC) == startup
    assert log.duration_ms is None
    assert log.error_message == INTERRUPTED_MESSAGE
    assert log.records_processed == 3
    assert log.metadata_json == '{"processed": 3}'
    run = recovery_db.get(RecordCheckRun, "1")
    assert run.status == "fail"
    assert run.ended_at.replace(tzinfo=UTC) == startup
    assert run.error_message == INTERRUPTED_MESSAGE
    assert (run.total_folders, run.healthy_count) == (5, 3)
    health = recovery_db.get(HealthCheckStatus, 1)
    assert health.is_running is False
    assert health.start_time is None
    assert health.completed_cameras == 3
    assert recovery_db.get(HealthCheckStatus, 2).is_running is True
    assert recovery_db.get(HealthCheckStatus, 3).start_time is not None
    for index, (status, _) in enumerate(cases[1:], start=2):
        assert recovery_db.get(JobExecutionLog, index).status == status
        assert recovery_db.get(RecordCheckRun, str(index)).status == status
    assert recover_interrupted_executions(recovery_db, startup) == {
        "jobs": 0,
        "record_checks": 0,
        "health_checks": 0,
    }


def test_recovery_rolls_back_if_a_later_update_fails(recovery_db: Session, monkeypatch) -> None:
    startup = datetime.now(UTC)
    recovery_db.add(
        JobExecutionLog(
            id=1,
            job_id="health_check",
            job_name="Health",
            status="running",
            started_at=startup - timedelta(minutes=1),
        )
    )
    recovery_db.commit()
    original_query = recovery_db.query

    def query(model):
        if model is RecordCheckRun:
            raise RuntimeError("Synthetic update failure")
        return original_query(model)

    monkeypatch.setattr(recovery_db, "query", query)
    with pytest.raises(RuntimeError, match="Synthetic update failure"):
        recover_interrupted_executions(recovery_db, startup)
    recovery_db.expire_all()
    assert recovery_db.get(JobExecutionLog, 1).status == "running"
    assert recovery_db.get(JobExecutionLog, 1).ended_at is None


def test_recovery_rejects_naive_startup_time() -> None:
    db = MagicMock()
    with pytest.raises(ValueError, match="timezone-aware"):
        recover_interrupted_executions(db, datetime(2026, 10, 15))
    db.query.assert_not_called()
