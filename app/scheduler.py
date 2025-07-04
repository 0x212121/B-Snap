from datetime import datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.onvif_client import load_active_cameras, load_cameras
from app.utils.snapshot_service import take_snapshot
from app.utils.health_check import ping_all_devices
from app.core.config import get_config
from app.models_sql import AuditLog, SnapshotLog
from app.utils.snapshot_utils import record_snapshot_metadata
from sqlalchemy.orm import Session
import concurrent.futures
import logging

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
    workers = get_config("snapshot_concurrent_workers", 5)
    cameras = load_active_cameras()

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

    scheduler.start()
    return scheduler


def update_scheduler_config():
    try:
        new_snapshot_interval = get_config("snapshot_interval_minutes", 600)
        new_healthcheck_interval = get_config("healthcheck_interval_minutes", 60)

        changed = False

        if new_snapshot_interval != last_config["snapshot_interval"]:
            scheduler.reschedule_job("scheduled_snapshot", trigger=IntervalTrigger(minutes=new_snapshot_interval))
            last_config["snapshot_interval"] = new_snapshot_interval
            print(f"[Scheduler] 🔁 Snapshot interval updated to {new_snapshot_interval} minutes.")
            changed = True

        if new_healthcheck_interval != last_config["healthcheck_interval"]:
            scheduler.reschedule_job("health_check", trigger=IntervalTrigger(minutes=new_healthcheck_interval))
            last_config["healthcheck_interval"] = new_healthcheck_interval
            print(f"[Scheduler] 🔁 Health check interval updated to {new_healthcheck_interval} minutes.")
            changed = True

        if changed:
            print("[Scheduler] ✅ Scheduler config updated and jobs rescheduled.")
        else:
            print("[Scheduler] ⏸ No config changes detected. Scheduler not updated.")

        return get_config("pagination_per_page", 10)

    except Exception as e:
        logger.warning(f"[Scheduler] ⚠️ Failed to reload scheduler config: {e}")



def delete_old_audit_logs():
    db: Session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)  # Retention: 90 hari
    deleted_count = db.query(AuditLog).filter(AuditLog.timestamp < cutoff).delete()
    db.commit()
    logger.info(f"Deleted {deleted_count} old audit logs")