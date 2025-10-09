from sqlalchemy import Column, Float, Integer, String, DateTime, func
from app.db.database import Base


class CommandLog(Base):
    __tablename__ = "command_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(String, index=True)
    command = Column(String, index=True)
    source = Column(String, default="whatsapp", index=True)


class ApiLog(Base):
    __tablename__ = "api_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(String, index=True)
    endpoint = Column(String, index=True)
    method = Column(String, index=True)
    status_code = Column(Integer, index=True)
    source = Column(String, default="web", index=True)
    duration_ms = Column(Float, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
