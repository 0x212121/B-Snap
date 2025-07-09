from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models_sql import Configuration

DEFAULT_CONFIG = {
    "snapshot_interval_minutes": "480",
    "healthcheck_interval_minutes": "15",
    "snapshot_concurrent_workers": "5",
    "items_per_page": "15",
    "max_screenshot_per_camera": "20",
    "watermark_text": "Property of ...",
    "map_title": "CCTV Maps"
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
