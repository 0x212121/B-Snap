from datetime import datetime
import pytz
from app.db.database import SessionLocal
from app.models.camera_daily_stats import CameraDailyStats
from app.models.config import Configuration


def get_configured_timezone(db):
    """Get timezone from database configuration."""
    cfg = db.query(Configuration).filter_by(key="timezone").first()
    return cfg.value if cfg else "UTC"


def check_stats(cam: object):
    db = SessionLocal()
    tz_name = get_configured_timezone(db)
    tz = pytz.timezone(tz_name)
    today = datetime.now(tz).date()
    # Check statistics
    camera_stat = db.query(CameraDailyStats).filter(
        CameraDailyStats.camera_name == cam.hostname,
        CameraDailyStats.date == today
    ).first()

    if camera_stat:
        camera_stat.snapshot_count += 1
    else:
        camera_stat = CameraDailyStats(
            camera_id=cam.id,
            camera_name=cam.hostname,
            date=today,
            snapshot_count=1
        )
        db.add(camera_stat)

    db.commit()