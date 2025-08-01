from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

class TaskTiming(Base):
    __tablename__ = 'task_timings'
    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String, nullable=False)
    device_id = Column(String, nullable=True)
    started_at = Column(DateTime, default=func.now(), nullable=False)
    ended_at = Column(DateTime, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
