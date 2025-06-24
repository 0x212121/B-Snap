from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.onvif_client import load_cameras
from app.utils.snapshot_service import take_snapshot
from app.utils.health_check import ping_all_devices
from app.core.config import get_config
from app.models_sql import AuditLog, CameraDailyStats, SnapshotLog, Camera as DBCamera
from app.utils.snapshot_utils import record_snapshot_metadata
from sqlalchemy.orm import Session
import concurrent.futures
import logging
import os

scheduler = BackgroundScheduler()
setup_logging()
logger = logging.getLogger("scheduler")


def run_snapshot(camera):
    db: Session = SessionLocal()
    try:
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
        else:
            logger.warning(f"[FAIL] Snapshot failed for {camera}: {result}")
    except Exception as e:
        logger.error(f"[ERROR] Snapshot failed for {camera.hostname}: {e}")
    finally:
        db.close()


def scheduled_snapshot():
    workers = get_config("snapshot_concurrent_workers", 5)
    cameras = load_cameras()

    logger.info(f"[SCHEDULED] Running snapshot for {len(cameras)} cameras with {workers} workers.")

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


def start_scheduler():
    snapshot_interval = get_config("snapshot_interval_minutes", 600)
    healthcheck_interval = get_config("healthcheck_interval_minutes", 60)
    workers = get_config("snapshot_concurrent_workers", 5)

    scheduler.add_job(scheduled_snapshot, 
                      trigger=IntervalTrigger(minutes=snapshot_interval), 
                      id='scheduled_snapshot', 
                      max_instances=workers,
                      coalesce=True,
                      misfire_grace_time=60)
    
    scheduler.add_job(
        ping_all_devices,
        trigger=IntervalTrigger(minutes=healthcheck_interval),
        id='health_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30
    )

    scheduler.add_job(delete_old_audit_logs, 'interval', days=1)

    scheduler.start()
    return scheduler


def update_scheduler_config():
    print(f"Scheduler: {scheduler.print_jobs}")

    try:
        snapshot_interval = get_config("snapshot_interval_minutes", 600)
        healthcheck_interval = get_config("healthcheck_interval_minutes", 60)

        scheduler.reschedule_job("scheduled_snapshot", trigger=IntervalTrigger(minutes=snapshot_interval))
        scheduler.reschedule_job("health_check", trigger=IntervalTrigger(minutes=healthcheck_interval))
        print("[Scheduler] ✅ Scheduler config reloaded.")
        print(f"""Snapshot interval : {snapshot_interval} min
Healthcheck interval: {healthcheck_interval} min""")

        jobs = scheduler.get_jobs()
        if not jobs:
            print("⚠️ No active jobs found.")
        else:
            print("✅ Active Jobs:")
            for job in jobs:
                print(f" - ID: {job.id}, Next Run: {job.next_run_time}")

        # Run scheduler every app start
        page_item = get_config("pagination_per_page", 10)
        return page_item
    except Exception as e:
        logger.info(f"[Scheduler] ⚠️ Failed to reload scheduler config: {e}")


def delete_old_audit_logs():
    db: Session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)  # Retention: 90 hari
    deleted_count = db.query(AuditLog).filter(AuditLog.timestamp < cutoff).delete()
    db.commit()
    logger.info(f"Deleted {deleted_count} old audit logs")