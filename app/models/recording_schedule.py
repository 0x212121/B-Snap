"""Archive schedules and durable per-camera recorder leases."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from app.db.database import Base


class RecordingSchedule(Base):
    """Keep archive recording independent of notification commands."""

    __tablename__ = "recording_schedules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    camera_id = Column(
        String(36), ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(120), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    mode = Column(String(20), nullable=False)
    interval_minutes = Column(Integer, nullable=False, default=15)
    cron = Column(String(100), nullable=False, default="")
    clip_seconds = Column(Integer, nullable=False, default=30)
    segment_seconds = Column(Integer, nullable=False, default=600)
    weekdays = Column(JSON, nullable=False, default=lambda: list(range(7)))
    start_time = Column(String(5), nullable=False, default="08:00")
    end_time = Column(String(5), nullable=False, default="17:00")
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_timezone = Column(String(100), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class CameraRecordingState(Base):
    """Atomically exclude concurrent manual and scheduled camera recordings."""

    __tablename__ = "camera_recording_states"

    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), primary_key=True)
    lease_owner = Column(String(36), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    schedule_id = Column(String(36), nullable=True)
    status = Column(String(20), nullable=False, default="idle")
    last_error = Column(Text, nullable=True)
    last_recorded_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
