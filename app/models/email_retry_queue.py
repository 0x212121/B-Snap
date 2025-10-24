from sqlalchemy import Column, String, DateTime, Integer, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.db.database import Base

class EmailRetryQueue(Base):
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
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    camera = relationship("Camera")
