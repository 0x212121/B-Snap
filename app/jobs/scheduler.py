from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.base import JobLookupError
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.onvif_client import load_active_cameras
from app.utils.snapshot_locker import get_camera_lock
from app.utils.snapshot_service import take_snapshot
from app.utils.health_check import ping_all_devices
from app.core.config import get_config
from app.models.audit_log import AuditLog
from app.models.camera_daily_stats import CameraDailyStats
from app.models.snapshot_log import SnapshotLog
from app.utils.snapshot_utils import record_snapshot_metadata
from sqlalchemy.orm import Session
from app.models.task_timing import TaskTiming
import concurrent.futures
import logging
from time import monotonic, sleep

scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce": True,
        "max_instances": 10,
        "misfire_grace_time": 60
    }
)

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
            db.commit()
            logger.info(f"[SUCCESS] Scheduled snapshot for {camera}, saved with ID {snapshot.id}")
            
            return {"status": "success"}
        else:
            logger.warning(f"[FAIL] Snapshot failed for {camera}: {result}")
            return {"status": "error", "details": result}
    except Exception as e:
        logger.error(f"[ERROR] Snapshot failed for {camera.hostname}: {e}")
        return {"status": "error", "details": str(e)}
    finally:
        db.close()


def scheduled_snapshot():
    db: Session = SessionLocal()
    workers = get_config("snapshot_concurrent_workers", 5)
    all_cameras = load_active_cameras()

    logger.info("[SCHEDULED] Running snapshot for %d cameras with %d workers, in batches.", len(all_cameras), workers)

    started_at = datetime.now(timezone.utc)
    time_start = monotonic()

    total_success_count = 0
    total_fail_count = 0

    BATCH_SIZE = get_config("snapshot_batch_size", 50)

    try:
        for i in range(0, len(all_cameras), BATCH_SIZE):
            current_batch = all_cameras[i:i + BATCH_SIZE]
            batch_number = int(i/BATCH_SIZE) + 1
            logger.info("[BATCH] Processing batch %d of %d cameras.", batch_number, len(current_batch))

            batch_success_count = 0
            batch_fail_count = 0

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_camera = {
                        executor.submit(run_snapshot, cam): cam for cam in current_batch
                    }

                    for future in concurrent.futures.as_completed(future_to_camera):
                        cam = future_to_camera[future]
                        try:
                            result = future.result()
                            if result and result.get("status") == "success":
                                batch_success_count += 1
                                logger.info("[SUCCESS] Snapshot taken for: %s (Batch)", cam.hostname)
                            else:
                                batch_fail_count += 1
                                logger.warning("[FAIL] Snapshot failed or returned error for: %s (Batch)", cam.hostname)
                        except Exception as e:
                            batch_fail_count += 1
                            logger.exception("[EXCEPTION] Unhandled error for %s (Batch): %s", cam.hostname, e)

            except Exception as e:
                logger.exception("[BATCH ERROR] Critical failure during batch snapshot processing: %s", e)
                
            total_success_count += batch_success_count
            total_fail_count += batch_fail_count
            logger.info("[BATCH SUMMARY] Batch complete: %d succeeded, %d failed.", batch_success_count, batch_fail_count)

            sleep_interval = get_config("snapshot_batch_delay_seconds", 5)
            logger.debug("Pausing for %d seconds before next batch.", sleep_interval)
            sleep(sleep_interval)

    except Exception as e:
        logger.exception("[SCHEDULER ERROR] Critical failure during scheduled snapshot: %s", e)

    finally:
        ended_at = datetime.now(timezone.utc)
        duration_ms = int((monotonic() - time_start) * 1000)

        if total_success_count == 0 and total_fail_count == 0:
            status = "no_camera"
        elif total_success_count == 0:
            status = "fail"
        elif total_fail_count == 0:
            status = "success"
        else:
            status = "partial"

        logger.info("[SUMMARY] Snapshot run complete: %d succeeded, %d failed.", total_success_count, total_fail_count)
        logger.info("[SUMMARY] Duration: %d ms", duration_ms)

        try:
            task_log = TaskTiming(
                task_name="scheduled_snapshot",
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                status=status,
            )
            logger.warning("[CONFIRM] Writing TaskTiming with status=%s, duration=%dms", status, duration_ms)
            db.add(task_log)
            db.commit()
            logger.warning("[CONFIRM] TaskTiming committed to DB")
        except Exception as e:
            logger.exception("[TimingLog] Failed to save snapshot timing log: %s", e)
        finally:
            db.close()


# Global state untuk menyimpan konfigurasi terakhir
last_config = {
    "snapshot_interval": None,
    "healthcheck_interval": None,
    "snapshot_concurrent_workers": None,
    "snapshot_batch_size": None,
    "snapshot_batch_delay_seconds": None,
}


def start_scheduler():
    snapshot_interval = get_config("snapshot_interval_minutes", 600)
    healthcheck_interval = get_config("healthcheck_interval_minutes", 60)
    workers = get_config("snapshot_concurrent_workers", 5)
    batch_size = get_config("snapshot_batch_size", 50)
    batch_delay = get_config("snapshot_batch_delay_seconds", 5)

    # Simpan konfigurasi awal ke global state
    last_config["snapshot_interval"] = snapshot_interval
    last_config["healthcheck_interval"] = healthcheck_interval
    last_config["snapshot_concurrent_workers"] = workers
    last_config["snapshot_batch_size"] = batch_size
    last_config["snapshot_batch_delay_seconds"] = batch_delay

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
        new_concurrent_workers = get_config("snapshot_concurrent_workers", 5)
        new_batch_size = get_config("snapshot_batch_size", 50)
        new_batch_delay = get_config("snapshot_batch_delay_seconds", 5)

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

        if new_concurrent_workers != last_config["snapshot_concurrent_workers"]:
            try:
                scheduler.modify_job("scheduled_snapshot", max_instances=new_concurrent_workers)
                last_config["snapshot_concurrent_workers"] = new_concurrent_workers
                logger.info("[Scheduler] 🔁 Concurrent workers updated to %d.", new_concurrent_workers)
                changed = True
            except JobLookupError:
                logger.warning("[Scheduler] ⚠️ Job 'scheduled_snapshot' not found during concurrent workers update.")

        if new_batch_size != last_config["snapshot_batch_size"]:
            last_config["snapshot_batch_size"] = new_batch_size
            logger.info("[Scheduler] 🔁 Snapshot batch size updated to %d.", new_batch_size)
            changed = True
            
        if new_batch_delay != last_config["snapshot_batch_delay_seconds"]:
            last_config["snapshot_batch_delay_seconds"] = new_batch_delay
            logger.info("[Scheduler] 🔁 Snapshot batch delay updated to %d seconds.", new_batch_delay)
            changed = True

        if changed:
            logger.info("[Scheduler] ✅ Scheduler config updated and jobs rescheduled.")
        else:
            logger.info("[Scheduler] ⏸ No config changes detected. Scheduler not updated.")

    except Exception as e:
        logger.warning(f"[Scheduler] ⚠️ Failed to reload scheduler config: {e}")


def delete_old_audit_logs():
    db: Session = SessionLocal()
    try:
        cutoff_date = date.today() - timedelta(days=180)
        deleted_rows = (
            db.query(AuditLog)
            .filter(AuditLog.timestamp < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("[delete_old_audit_logs] Deleted %d rows older than %s", deleted_rows, cutoff_date)
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_audit_logs] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def delete_old_camera_stats():
    db: Session = SessionLocal()
    try:
        cutoff_date = date.today() - timedelta(days=90)
        deleted_rows = (
            db.query(CameraDailyStats)
            .filter(CameraDailyStats.date < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("[delete_old_camera_stats] Deleted %d rows older than %s", deleted_rows, cutoff_date)
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_camera_stats] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()