import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class CameraStatusChangeLog(Base):
    __tablename__ = "camera_status_change_log"
    id = Column(String(36), primary_key=True, default=generate_uuid, unique=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True)
    previous_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    duration_since_last_change = Column(Integer, nullable=True)
    camera = relationship("Camera", backref="status_change_logs")
