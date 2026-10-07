"""API request totals retained after detailed logs expire."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, Date, String

from app.db.database import Base


class ApiDailyStats(Base):
    """Archived totals identified by the local day and its original timezone."""

    __tablename__ = "api_daily_stats"

    date = Column(Date, primary_key=True)
    timezone = Column(String(64), primary_key=True)
    request_count = Column(BigInteger, nullable=False)
