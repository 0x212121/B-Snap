from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.config import Configuration

DEFAULT_CONFIG = {
    "snapshot_interval_minutes": "480",
    "healthcheck_interval_minutes": "15",
    "snapshot_concurrent_workers": "5",
    "max_screenshot_per_camera": "20",
    "watermark_text": "Property of ...",
    "map_title": "CCTV Maps",
    "snapshot_batch_size": "50",  # Default value for batch size
    "snapshot_batch_delay_seconds": "5", # Default value for batch delay
    # Log retention defaults
    "retention_audit_logs_days": "180",
    "retention_api_logs_days": "90",
    "retention_command_logs_days": "90",
    "retention_camera_stats_days": "90",
    "retention_email_logs_days": "90",
    # Storage monitoring defaults
    "storage_critical_percent": "95",
    "storage_warning_percent": "85",
    "storage_info_percent": "75",
    "storage_critical_free_gb": "5",
    # SMTP defaults (empty - user must configure)
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_pass": "",
    "email_from": "",
    "email_cc": "",
    # GoWA (WhatsApp Gateway) defaults
    "gowa_enabled": "0",
    "gowa_base_url": "http://localhost:3000",
    "gowa_api_key": "",
    "gowa_default_receiver": ""
}

def seed_config(db: Session):
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