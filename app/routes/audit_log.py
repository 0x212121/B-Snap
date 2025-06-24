from sqlalchemy import Column, DateTime, Integer, String
from datetime import datetime, timezone
from app.db.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.now(timezone.utc), nullable=False)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    ip = Column(String, nullable=True)
    extra = Column(String, nullable=True)