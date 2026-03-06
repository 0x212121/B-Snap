from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from app.db.database import Base


class RememberToken(Base):
    """
    Persistent login tokens for "Remember Me" functionality.
    Tokens do not expire (or expire in very long time) until manually revoked.
    """
    __tablename__ = "remember_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    
    # Optional: device info for user visibility
    device_name = Column(String(100), nullable=True)  # e.g., "Chrome on Windows"
    ip_address = Column(String(45), nullable=True)    # IPv6 max 45 chars
    user_agent = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<RememberToken(id={self.id}, user_id={self.user_id}, device={self.device_name})>"
