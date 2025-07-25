from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.base import JobLookupError
from fastapi import Depends
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.onvif_client import load_active_cameras
from app.db.database import get_db
from app.utils.snapshot_locker import get_camera_lock
from app.utils.snapshot_service import take_snapshot
from app.utils.health_check import ping_all_devices
from app.core.config import get_config
from app.models_sql import AuditLog, CameraDailyStats, SnapshotLog
from app.utils.snapshot_utils import record_snapshot_metadata
from sqlalchemy.orm import Session
from app.models_sql import TaskTiming
import concurrent.futures
import logging
from time import monotonic
from datetime import datetime

scheduler = BackgroundScheduler()
setup_logging()
logger = logging.getLogger("scheduler")


def run_snapshot(camera):
    db: Session = SessionLocal()
    lock = get_camera_lock(str(camera.id))
    
    try:
        with lock:
            result = take_snapshot(camera, db)
        if result["status"] == "success":
            path = result["file_path"]
            snapshot = record_snapshot_metadata(
                db=db,
                camera_id=camera.id,
                file_path=path,
                resolution=result.get("resolution", "N/A"),
            )  
            snapshot_log = SnapshotLog(id=str(uuid4()), camera_name=camera.hostname)

            db.add(snapshot_log)
            logger.info(f"[SUCCESS] Scheduled snapshot for {camera}, saved with ID {snapshot.id}")
            
            return {"status": "success"}  # ✅ Tambahkan return jika sukses
        else:
            logger.warning(f"[FAIL] Snapshot failed for {camera}: {result}")
            return {"status": "error", "details": result}  # ✅ Tambahkan return jika gagal
    except Exception as e:
        logger.error(f"[ERROR] Snapshot failed for {camera.hostname}: {e}")
        return {"status": "error", "details": str(e)}  # ✅ Tambahkan return jika exception
    finally:
        db.close()


def scheduled_snapshot():
    db: Session = SessionLocal()
    workers = get_config("snapshot_concurrent_workers", 5)
    cameras = load_active_cameras()

    logger.info(f"[SCHEDULED] Running snapshot for {len(cameras)} cameras with {workers} workers.")

    started_at = datetime.utcnow()
    time_start = monotonic()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_camera = {
                executor.submit(run_snapshot, cam): cam for cam in cameras
            }

            for future in concurrent.futures.as_completed(future_to_camera):
                cam = future_to_camera[future]
                try:
                    result = future.result()
                    if result and result.get("status") == "success":
                        logger.info(f"[SUCCESS] Snapshot taken for: {cam.hostname}")
                    else:
                        logger.warning(f"[FAIL] Snapshot failed or returned error for: {cam.hostname}")
                except Exception as e:
                    logger.error(f"[EXCEPTION] Unhandled error for {cam.hostname}: {e}")
    finally:
        ended_at = datetime.utcnow()
        duration_ms = int((monotonic() - time_start) * 1000)

        task_log = TaskTiming(
            task_name='scheduled_snapshot',
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            status='success'
        )
        try:
            db.add(task_log)
            db.commit()
        except Exception as e:
            logger.warning(f"[TimingLog] Failed to save snapshot timing log: {e}")
        db.close()


# Global state untuk menyimpan konfigurasi terakhir
last_config = {
    "snapshot_interval": None,
    "healthcheck_interval": None
}


def start_scheduler():
    snapshot_interval = get_config("snapshot_interval_minutes", 600)
    healthcheck_interval = get_config("healthcheck_interval_minutes", 60)
    workers = get_config("snapshot_concurrent_workers", 5)

    # Simpan konfigurasi awal ke global state
    last_config["snapshot_interval"] = snapshot_interval
    last_config["healthcheck_interval"] = healthcheck_interval

    scheduler.add_job(
        scheduled_snapshot,
        trigger=IntervalTrigger(minutes=snapshot_interval),
        id='scheduled_snapshot',
        max_instances=workers,
        coalesce=True,
        misfire_grace_time=60
    )

    scheduler.add_job(
        ping_all_devices,
        trigger=IntervalTrigger(minutes=healthcheck_interval),
        id='health_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30
    )

    scheduler.add_job(delete_old_audit_logs, 'interval', days=1)
    scheduler.add_job(delete_old_camera_stats, 'interval', days=1)

    scheduler.start()
    return scheduler


def update_scheduler_config():
    try:
        new_snapshot_interval = get_config("snapshot_interval_minutes", 600)
        new_healthcheck_interval = get_config("healthcheck_interval_minutes", 60)

        changed = False

        if new_snapshot_interval != last_config["snapshot_interval"]:
            try:
                scheduler.reschedule_job("scheduled_snapshot", trigger=IntervalTrigger(minutes=new_snapshot_interval))
                last_config["snapshot_interval"] = new_snapshot_interval
                logger.info("[Scheduler] 🔁 Snapshot interval updated to %s minutes.", new_snapshot_interval)
                changed = True
            except JobLookupError:
                logger.warning("[Scheduler] ⚠️ Job 'scheduled_snapshot' not found during config update.")

        if new_healthcheck_interval != last_config["healthcheck_interval"]:
            try:
                scheduler.reschedule_job("health_check", trigger=IntervalTrigger(minutes=new_healthcheck_interval))
                last_config["healthcheck_interval"] = new_healthcheck_interval
                logger.info("[Scheduler] 🔁 Health check interval updated to %s minutes.", new_healthcheck_interval)
                changed = True
            except JobLookupError:
                logger.warning("[Scheduler] ⚠️ Job 'health_check' not found during config update.")

        if changed:
            logger.info("[Scheduler] ✅ Scheduler config updated and jobs rescheduled.")
        else:
            logger.info("[Scheduler] ⏸ No config changes detected. Scheduler not updated.")

    except Exception as e:
        logger.warning(f"[Scheduler] ⚠️ Failed to reload scheduler config: {e}")


def delete_old_audit_logs():
    db: Session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)  # Retention: 90 hari
    deleted_count = db.query(AuditLog).filter(AuditLog.timestamp < cutoff).delete()
    db.commit()
    logger.info(f"Deleted {deleted_count} old audit logs")


def delete_old_camera_stats(db: Session = Depends(get_db)):
    """Delete camera_daily_stats records older than 90 days."""
    cutoff_date = date.today() - timedelta(days=90)
    deleted_rows = db.query(CameraDailyStats).filter(CameraDailyStats.date < cutoff_date).delete()
    db.commit()
    return deleted_rows