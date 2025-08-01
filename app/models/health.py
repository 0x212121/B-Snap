import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class CameraHealth(Base):
    __tablename__ = "camera_health"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    status = Column(String, default="Unknown", nullable=False)
    status_changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    latency = Column(Integer)
    checked = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_online = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    type = Column(String, default="Camera", nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True)
    camera = relationship("Camera", back_populates="health")
    nvr_id = Column(String(36), ForeignKey("nvr.id", ondelete="CASCADE"), nullable=True)
    nvr = relationship("NVR", back_populates="health")
