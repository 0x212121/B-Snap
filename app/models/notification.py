"""Notification model for toast notifications.

This module provides the Notification model for persistent toast notifications.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.database import Base


class Notification(Base):
    """Database model for persistent toast notifications."""
    
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    type = Column(String(20), default="info")
    user_id = Column(Integer, nullable=True)  # Null = broadcast to all
    camera_id = Column(Integer, nullable=True)
    is_read = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict[str, Any]:
        """Convert notification to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "type": self.type,
            "user_id": self.user_id,
            "camera_id": self.camera_id,
            "is_read": self.is_read.isoformat() if self.is_read else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
