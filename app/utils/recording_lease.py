"""Short database leases shared by manual recording and archive workers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.recording_schedule import CameraRecordingState

LEASE_SECONDS = 60
MANUAL_LEASE_SECONDS = 600


def require_recording_available(db: Session, camera_id: str) -> None:
    """Report a busy camera before accepting an asynchronous browser/API recording."""
    state = db.get(CameraRecordingState, camera_id)
    if state and state.lease_owner and state.lease_until:
        expiry = state.lease_until
        expiry = expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry
        if expiry > datetime.now(UTC):
            raise HTTPException(status_code=409, detail="Camera is already recording")


def acquire_recording_lease(
    camera_id: str, schedule_id: str | None = None, *, seconds: int = LEASE_SECONDS
) -> str | None:
    """Claim one camera atomically across processes without retaining a DB connection."""
    now = datetime.now(UTC)
    owner = str(uuid4())
    available = or_(
        CameraRecordingState.lease_until.is_(None), CameraRecordingState.lease_until <= now
    )
    if schedule_id is None:
        available = or_(available, CameraRecordingState.lease_owner.is_(None))
    with SessionLocal() as db:
        insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
        db.execute(
            insert(CameraRecordingState)
            .values(camera_id=camera_id, status="idle")
            .on_conflict_do_nothing(index_elements=["camera_id"])
        )
        result = db.execute(
            update(CameraRecordingState)
            .where(
                CameraRecordingState.camera_id == camera_id,
                available,
            )
            .values(
                lease_owner=owner,
                lease_until=now + timedelta(seconds=seconds),
                schedule_id=schedule_id,
                status="starting",
                heartbeat_at=now,
            )
        )
        db.commit()
        return owner if result.rowcount else None


def renew_recording_lease(camera_id: str, owner: str) -> bool:
    """Fence expired owners; a recorder must stop immediately if renewal fails."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        result = db.execute(
            update(CameraRecordingState)
            .where(
                CameraRecordingState.camera_id == camera_id,
                CameraRecordingState.lease_owner == owner,
                CameraRecordingState.lease_until > now,
            )
            .values(
                lease_until=now + timedelta(seconds=LEASE_SECONDS),
                heartbeat_at=now,
                status="recording",
            )
        )
        db.commit()
        return bool(result.rowcount)


def release_recording_lease(camera_id: str, owner: str, error: str | None = None) -> None:
    """Keep failure cooldowns while preventing stale workers from clearing a new lease."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.execute(
            update(CameraRecordingState)
            .where(
                CameraRecordingState.camera_id == camera_id,
                CameraRecordingState.lease_owner == owner,
            )
            .values(
                lease_owner=None,
                lease_until=now + timedelta(seconds=60 if error else 0),
                status="error" if error else "idle",
                last_error=error,
                heartbeat_at=now,
            )
        )
        db.commit()
