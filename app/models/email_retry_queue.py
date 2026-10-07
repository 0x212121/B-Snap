from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class EmailRetryQueue(Base):
    """Track a notification incident through pending and terminal retry states."""

    __tablename__ = "email_retry_queue"

    id = Column(String, primary_key=True)
    camera_id = Column(String, ForeignKey("cameras.id"), nullable=False)
    type = Column(String, nullable=False)  # e.g. "tamper" or "recovery"
    reason = Column(Text, nullable=True)
    file_path = Column(String, nullable=True)
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    last_attempt = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    sent = Column(Boolean, default=False)
    status = Column(String(16), nullable=False, default="pending", server_default="pending")
    incident_time = Column(DateTime(timezone=True), nullable=False)
    offline_duration_seconds = Column(Integer, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    camera = relationship("Camera")

    __table_args__ = (
        Index(
            "ix_email_retry_due",
            "next_retry_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'exhausted', 'cancelled')",
            name="ck_email_retry_status",
        ),
        Index(
            "ux_email_retry_active",
            "camera_id",
            "type",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )
