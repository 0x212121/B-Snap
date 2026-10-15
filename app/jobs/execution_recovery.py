"""Recover persisted execution state before the single scheduler worker starts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.health_check_status import HealthCheckStatus
from app.models.job_execution_log import JobExecutionLog
from app.models.record_check import RecordCheckRun

INTERRUPTED_MESSAGE = (
    "Execution interrupted by service shutdown or restart; recovered at scheduler startup. "
    "The exact interruption time is unknown."
)


def recover_interrupted_executions(db: Session, startup_at: datetime) -> dict[str, int]:
    """Close unfinished runs predating startup in one transaction.

    Call only before starting the dedicated scheduler, never on web startup or
    configuration reload: the scheduler may still be executing jobs then.
    Recovery time is recorded as ended_at; duration stays unknown rather than
    including service downtime in execution statistics. Partial results remain.
    """
    if startup_at.tzinfo is None:
        raise ValueError("startup_at must be timezone-aware")
    startup_at = startup_at.astimezone(UTC)
    try:
        jobs = (
            db.query(JobExecutionLog)
            .filter(JobExecutionLog.status == "running", JobExecutionLog.started_at < startup_at)
            .update(
                {
                    JobExecutionLog.status: "fail",
                    JobExecutionLog.ended_at: startup_at,
                    JobExecutionLog.duration_ms: None,
                    JobExecutionLog.error_message: INTERRUPTED_MESSAGE,
                },
                synchronize_session=False,
            )
        )
        record_checks = (
            db.query(RecordCheckRun)
            .filter(RecordCheckRun.status == "running", RecordCheckRun.started_at < startup_at)
            .update(
                {
                    RecordCheckRun.status: "fail",
                    RecordCheckRun.ended_at: startup_at,
                    RecordCheckRun.error_message: INTERRUPTED_MESSAGE,
                },
                synchronize_session=False,
            )
        )
        health_checks = (
            db.query(HealthCheckStatus)
            .filter(
                HealthCheckStatus.is_running.is_(True),
                or_(
                    HealthCheckStatus.start_time < startup_at,
                    HealthCheckStatus.start_time.is_(None),
                ),
            )
            .update(
                {HealthCheckStatus.is_running: False, HealthCheckStatus.start_time: None},
                synchronize_session=False,
            )
        )
        db.commit()
        return {"jobs": jobs, "record_checks": record_checks, "health_checks": health_checks}
    except Exception:
        db.rollback()
        raise
