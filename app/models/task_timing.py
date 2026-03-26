from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime
from app.db.database import Base

def _utc_now():
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)

class TaskTiming(Base):
    __tablename__ = 'task_timings'
    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String, nullable=False)
    device_id = Column(String, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_utc_now, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=False)
    duration_ms = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
