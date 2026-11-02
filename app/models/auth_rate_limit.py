"""Shared authentication-attempt windows across web worker processes."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String

from app.db.database import Base


class AuthRateLimit(Base):
    """Store only a digest of the identity/IP scope, never credentials."""

    __tablename__ = "auth_rate_limits"

    bucket = Column(String(64), primary_key=True)
    attempts = Column(Integer, nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False, index=True)
