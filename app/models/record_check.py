import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class RecordSource(Base):
    __tablename__ = "record_sources"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(120), nullable=False, unique=True, index=True)
    base_path = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    nvr_id = Column(String(36), ForeignKey("nvr.id", ondelete="SET NULL"), nullable=True)
    stale_threshold_seconds = Column(Integer, default=4200, nullable=False)
    long_dead_threshold_seconds = Column(Integer, default=604800, nullable=False)
    scan_depth = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    nvr = relationship("NVR")
    runs = relationship("RecordCheckRun", back_populates="source", cascade="all, delete-orphan")
    statuses = relationship("RecordFolderStatus", back_populates="source", cascade="all, delete-orphan")


class RecordCheckRun(Base):
    __tablename__ = "record_check_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_id = Column(String(36), ForeignKey("record_sources.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="running")
    total_folders = Column(Integer, default=0, nullable=False)
    healthy_count = Column(Integer, default=0, nullable=False)
    stale_count = Column(Integer, default=0, nullable=False)
    long_dead_count = Column(Integer, default=0, nullable=False)
    unknown_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)

    source = relationship("RecordSource", back_populates="runs")
    checks = relationship("RecordFolderCheck", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_record_check_runs_source_started", "source_id", "started_at"),
        Index("idx_record_check_runs_status", "status"),
    )


class RecordFolderCheck(Base):
    __tablename__ = "record_folder_checks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("record_check_runs.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(String(36), ForeignKey("record_sources.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    folder_name = Column(String(255), nullable=False)
    folder_path = Column(Text, nullable=False)
    last_mtime = Column(DateTime(timezone=True), nullable=True)
    age_seconds = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False)
    checked_at = Column(DateTime(timezone=True), nullable=False)

    run = relationship("RecordCheckRun", back_populates="checks")
    source = relationship("RecordSource")
    camera = relationship("Camera")

    __table_args__ = (
        Index("idx_record_folder_checks_run", "run_id"),
        Index("idx_record_folder_checks_source_status", "source_id", "status"),
        Index("idx_record_folder_checks_camera", "camera_id"),
    )


class RecordFolderStatus(Base):
    __tablename__ = "record_folder_statuses"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_id = Column(String(36), ForeignKey("record_sources.id", ondelete="CASCADE"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    folder_name = Column(String(255), nullable=False)
    folder_path = Column(Text, nullable=False)
    last_mtime = Column(DateTime(timezone=True), nullable=True)
    age_seconds = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="unknown")
    status_changed_at = Column(DateTime(timezone=True), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), nullable=False)
    alert_active = Column(Boolean, default=False, nullable=False)
    last_alert_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_recovery_sent_at = Column(DateTime(timezone=True), nullable=True)

    source = relationship("RecordSource", back_populates="statuses")
    camera = relationship("Camera")

    __table_args__ = (
        UniqueConstraint("source_id", "folder_name", name="uq_record_folder_status_source_folder"),
        Index("idx_record_folder_status_source_status", "source_id", "status"),
        Index("idx_record_folder_status_camera", "camera_id"),
    )


class RecordFolderMapping(Base):
    __tablename__ = "record_folder_mappings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_id = Column(String(36), ForeignKey("record_sources.id", ondelete="CASCADE"), nullable=False)
    folder_name = Column(String(255), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    source = relationship("RecordSource")
    camera = relationship("Camera")

    __table_args__ = (
        UniqueConstraint("source_id", "folder_name", name="uq_record_folder_mapping_source_folder"),
        Index("idx_record_folder_mapping_camera", "camera_id"),
    )


class RecordStatusEvent(Base):
    __tablename__ = "record_status_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_id = Column(String(36), ForeignKey("record_sources.id", ondelete="CASCADE"), nullable=False)
    folder_status_id = Column(
        String(36), ForeignKey("record_folder_statuses.id", ondelete="CASCADE"), nullable=False
    )
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    folder_name = Column(String(255), nullable=False)
    previous_status = Column(String(20), nullable=True)
    new_status = Column(String(20), nullable=False)
    event_type = Column(String(30), nullable=False)
    message = Column(Text, nullable=True)
    notification_sent = Column(Boolean, default=False, nullable=False)
    notification_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    source = relationship("RecordSource")
    folder_status = relationship("RecordFolderStatus")
    camera = relationship("Camera")

    __table_args__ = (
        Index("idx_record_status_events_source_created", "source_id", "created_at"),
        Index("idx_record_status_events_type", "event_type"),
    )
