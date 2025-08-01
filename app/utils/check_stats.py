from datetime import date
from app.db.database import SessionLocal
from app.models.camera_daily_stats import CameraDailyStats


def check_stats(cam: object):
    today = date.today()
    # Check statistics
    db = SessionLocal()
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