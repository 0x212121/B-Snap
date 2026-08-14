from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.config import Configuration

DEFAULT_CONFIG = {
    # Snapshot settings
    "snapshot_interval_minutes": "480",
    "snapshot_concurrent_workers": "5",
    "max_screenshot_per_camera": "20",
    "snapshot_batch_size": "50",
    "snapshot_batch_delay_seconds": "5",
    "snapshot_ping_check_enabled": "true",
    "snapshot_ping_timeout_ms": "3000",
    
    # Health check settings
    "healthcheck_interval_minutes": "15",
    
    # Map settings
    "map_title": "CCTV Maps",
    "watermark_text": "Property of ...",
    
    # Log retention settings
    "retention_audit_logs_days": "180",
    "retention_api_logs_days": "90",
    "retention_command_logs_days": "90",
    "retention_camera_stats_days": "90",
    "retention_email_logs_days": "90",
    "retention_job_logs_days": "30",  # New: job execution log retention
    
    # Storage monitoring settings
    "storage_critical_percent": "95",
    "storage_warning_percent": "85",
    "storage_info_percent": "75",
    "storage_critical_free_gb": "5",
    
    # SMTP settings
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_security": "starttls",
    "email_from": "",
    "email_cc": "",
    
    # Email retry settings
    "email_retry_interval_minutes": "10",  # New: configurable email retry interval
    "email_retry_max_attempts": "5",
    
    # Cleanup job settings
    "storage_check_interval_hours": "1",  # New: storage check interval
    "cleanup_interval_days": "1",  # New: configurable cleanup interval
    "record_check_interval_minutes": "10",
    "retention_record_check_days": "90",
    "record_check_daily_report_enabled": "1",
    "app_public_url": "",
    
    # Job Cron Schedules (NEW) - Cron expressions override interval settings
    # Format: "minute hour day month weekday" (e.g., "0 8,13,23 * * *" = jam 8, 13, 23)
    "snapshot_cron": "",  # Empty = use interval, e.g., "0 8,13,23 * * *" for specific times
    "healthcheck_cron": "",  # Empty = use interval
    "storage_check_cron": "",  # Empty = use interval
    "record_check_cron": "",  # Empty = use interval
    "cleanup_cron": "0 2 * * *",  # Default: 2 AM daily
    "email_retry_cron": "",  # Empty = use interval
    
    # WhatsApp Gateway settings
    "gowa_enabled": "0",
    "gowa_base_url": "http://localhost:3000",
    "gowa_api_key": "",
    "gowa_default_receiver": "",
    "wa_daily_report_hour": "8",  # New: WA daily report hour (0-23)
    "wa_daily_report_minute": "0",  # New: WA daily report minute (0-59)
    "wa_storage_alert_interval_hours": "2",  # New: WA storage alert interval
}


def seed_config(db: Session):
    """Initialize default configuration values in database.
    
    Only inserts missing keys, never overwrites existing values.
    """
    try:
        for key, value in DEFAULT_CONFIG.items():
            existing = db.query(Configuration).filter_by(key=key).first()
            if not existing:
                config = Configuration(key=key, value=str(value))
                db.add(config)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        print(f"[seed_config] Failed to seed config: {e}")
