from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger
from app.db.database import Base


class StorageMetric(Base):
    """
    Stores storage usage metrics over time for trend analysis.
    """
    __tablename__ = "storage_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    
    # Disk usage (in bytes)
    total_bytes = Column(BigInteger, nullable=False)
    used_bytes = Column(BigInteger, nullable=False)
    free_bytes = Column(BigInteger, nullable=False)
    usage_percent = Column(Float, nullable=False)
    
    # Breakdown by category (in bytes)
    snapshots_bytes = Column(BigInteger, default=0)
    videos_bytes = Column(BigInteger, default=0)
    logs_bytes = Column(BigInteger, default=0)
    other_bytes = Column(BigInteger, default=0)
    
    # Calculated metrics
    daily_growth_rate = Column(Float, default=0)  # GB per day
    days_until_full = Column(Float, default=0)    # Predicted days until 100%
    
    # Alert status
    alert_level = Column(String(20), default="normal")  # normal, info, warning, critical
    alert_message = Column(String(500), nullable=True)


class StorageAlert(Base):
    """
    Stores storage alert history.
    """
    __tablename__ = "storage_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    level = Column(String(20), nullable=False)  # info, warning, critical
    message = Column(String(500), nullable=False)
    usage_percent = Column(Float, nullable=False)
    free_gb = Column(Float, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(100), nullable=True)
