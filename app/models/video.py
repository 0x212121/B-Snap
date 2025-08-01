import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Video(Base):
    __tablename__ = "videos"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    camera_name = Column(String, nullable=False)
    camera_ip = Column(String, nullable=True)
    camera_group = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    file_path = Column(String, nullable=False, unique=True)
    file_size = Column(Integer)
    duration = Column(Integer)
    resolution = Column(String(12), default="Unknown")
    camera = relationship("Camera", back_populates="videos")
