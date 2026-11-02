from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey
from app.db.database import Base

class HealthCheckStatus(Base):
    __tablename__ = "health_check_status"
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(
        Integer, ForeignKey("camera_groups.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    is_running = Column(Boolean, default=False)
    start_time = Column(DateTime(timezone=True))
    total_cameras = Column(Integer, default=0)
    completed_cameras = Column(Integer, default=0)
