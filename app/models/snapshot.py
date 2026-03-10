import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    camera_name = Column(String, nullable=False)
    camera_ip = Column(String, nullable=False)
    camera_port = Column(Integer, default=80)
    camera_location = Column(String)
    camera_group = Column(String)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    file_path = Column(String, nullable=False)
    file_size = Column(Integer)
    resolution = Column(String)
    is_tampered = Column(Boolean, default=False)
    tamper_reason = Column(String, nullable=True)
    blur_score = Column(Float, nullable=True)
    entropy_score = Column(Float, nullable=True)
    is_orphaned = Column(Boolean, default=False, server_default="0")
    camera = relationship("Camera", back_populates="snapshots")
