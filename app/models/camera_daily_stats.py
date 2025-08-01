from datetime import datetime, date, timezone
from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

class CameraDailyStats(Base):
    __tablename__ = "camera_daily_stats"

    id = Column(Integer, primary_key=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=True)
    camera_name = Column(String(60), nullable=False)
    date = Column(Date, nullable=False, default=date.today)
    uptime_percentage = Column(Float, default=0.0)
    snapshot_count = Column(Integer, default=0)
    total_uptime_seconds = Column(Integer, default=0)
    total_downtime_seconds = Column(Integer, default=0)
    checked = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    camera = relationship("Camera", back_populates="daily_stats")
