"""Separate progress trackers for global and group-scoped health requests."""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.health_check_status import HealthCheckStatus


def health_progress(
    db: Session, group_id: int | None, *, create: bool = False
) -> HealthCheckStatus | None:
    """Lock a scope's progress; global ID 1 remains compatible with scheduler jobs."""
    # Negative scoped IDs cannot consume the legacy singleton's reserved ID 1.
    tracker_id = 1 if group_id is None else -group_id
    if create:
        insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
        statement = (
            insert(HealthCheckStatus)
            .values(
                id=tracker_id,
                group_id=group_id,
                is_running=False,
                total_cameras=0,
                completed_cameras=0,
            )
            .on_conflict_do_nothing(index_elements=[HealthCheckStatus.id])
        )
        db.execute(statement)
    return (
        db.query(HealthCheckStatus)
        .filter(HealthCheckStatus.id == tracker_id, HealthCheckStatus.group_id == group_id)
        .with_for_update()
        .first()
    )
