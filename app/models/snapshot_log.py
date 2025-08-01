import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class SnapshotLog(Base):
    __tablename__ = "snapshot_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid, index=True)
    camera_name = Column(String, ForeignKey("cameras.hostname", ondelete="SET NULL"), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    camera = relationship("Camera", back_populates="snapshot_logs")
