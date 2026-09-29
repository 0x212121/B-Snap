# app/models/camera_email_notification_log.py
from sqlalchemy import Column, String, DateTime, Integer, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.db.database import Base


class CameraEmailNotificationLog(Base):
    __tablename__ = "camera_email_notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(String, ForeignKey("cameras.id", ondelete="SET NULL"), index=True, nullable=True)
    camera_name = Column(String, nullable=True)
    incident_started_at = Column(DateTime(timezone=True), nullable=False)
    type = Column(String, nullable=True, default="alert")
    sent_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    success = Column(Boolean, default=False, nullable=False)
    error_message = Column(String, nullable=True)
    reason = Column(String(64), nullable=True)

    camera = relationship("Camera", back_populates="email_logs", passive_deletes=True)
    recipients = relationship("CameraEmailNotificationRecipient", back_populates="log", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint("camera_id", "incident_started_at", name="uq_camera_incident_once"),
    )

class CameraEmailNotificationRecipient(Base):
    __tablename__ = "camera_email_notification_log_recipients"

    id = Column(Integer, primary_key=True, index=True)
    log_id = Column(Integer, ForeignKey("camera_email_notification_logs.id", ondelete="CASCADE"))
    recipient_email = Column(String, nullable=False)

    log = relationship("CameraEmailNotificationLog", back_populates="recipients")
