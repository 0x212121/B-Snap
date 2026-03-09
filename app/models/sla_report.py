"""SLA Report model for compliance tracking and enterprise monitoring."""

import uuid
from datetime import datetime, timezone, date
from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from app.db.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class SLAReport(Base):
    """SLA (Service Level Agreement) compliance report per camera per period."""
    
    __tablename__ = "sla_reports"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    
    # Uptime metrics
    uptime_percentage = Column(Float, default=0.0)
    total_uptime_seconds = Column(Integer, default=0)
    total_downtime_seconds = Column(Integer, default=0)
    
    # MTTR (Mean Time To Recovery) in seconds
    mttr_seconds = Column(Integer, nullable=True)
    
    # MTBF (Mean Time Between Failures) in seconds
    mtbf_seconds = Column(Integer, nullable=True)
    
    # Incident counts
    incident_count = Column(Integer, default=0)
    severity_critical = Column(Integer, default=0)
    severity_major = Column(Integer, default=0)
    severity_minor = Column(Integer, default=0)
    
    # Compliance status
    compliance_status = Column(String(20), default="Unknown")  # Pass/Fail/Warning
    sla_threshold = Column(Float, default=99.5)  # Target SLA threshold
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    camera = relationship("Camera", backref="sla_reports")


class ScheduledReport(Base):
    """Configuration for auto-scheduled health reports."""
    
    __tablename__ = "scheduled_reports"
    
    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    report_type = Column(String(20), default="health")  # health, sla, compliance
    format = Column(String(10), default="pdf")  # pdf, excel, csv
    
    # Schedule configuration
    frequency = Column(String(20), default="weekly")  # daily, weekly, monthly
    day_of_week = Column(Integer, nullable=True)  # 0-6 for weekly (Monday=0)
    day_of_month = Column(Integer, nullable=True)  # 1-31 for monthly
    hour = Column(Integer, default=8)  # Hour to send (0-23)
    timezone = Column(String(50), default="UTC")
    
    # Report parameters
    lookback_days = Column(Integer, default=7)  # Days of data to include
    include_charts = Column(Boolean, default=True)
    sla_threshold = Column(Float, default=99.5)
    
    # Recipients (stored as JSON string of email list)
    recipients = Column(String(500), nullable=True)
    
    # Status
    is_active = Column(Boolean, default=True)
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    next_scheduled_at = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    
    # Relationships
    creator = relationship("User", backref="scheduled_reports")
