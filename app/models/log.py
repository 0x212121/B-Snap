from sqlalchemy import Column, Float, Integer, String, DateTime, Text, func
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


class WhatsAppMessageLog(Base):
    __tablename__ = "whatsapp_message_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    phone_number = Column(String(32), nullable=False, index=True)
    provider_message_id = Column(String(200), nullable=True, unique=True, index=True)
    direction = Column(String(8), nullable=False, index=True)
    status = Column(String(24), nullable=False, index=True)
    command = Column(String(120), nullable=True)
    message = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
