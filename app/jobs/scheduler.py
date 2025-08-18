from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.base import JobLookupError
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.snapshot import load_active_cameras
from app.utils.snapshot_locker import get_camera_lock
from app.utils.snapshot_service import take_snapshot
from app.utils.healthcheck import ping_all_devices
from app.core.config import get_config
from app.models.audit_log import AuditLog
from app.models.camera_daily_stats import CameraDailyStats
from app.models.snapshot_log import SnapshotLog
from app.utils.snapshot_utils import record_snapshot_metadata
from sqlalchemy.orm import Session
from app.models.task_timing import TaskTiming
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import psutil
import os
from time import monotonic, sleep

# --- Scheduler Init ---
scheduler = BackgroundScheduler(
    job_defaults={
        "coalesce": True,
        "max_instances": 10,
        "misfire_grace_time": 60
    }
)

setup_logging()
logger = logging.getLogger("scheduler")

# --- Thread Pool ---
WORKERS = int(get_config("snapshot_concurrent_workers", 5))
thread_pool = ThreadPoolExecutor(max_workers=WORKERS)

# --- Global Config State ---
last_config = {
    "snapshot_interval_minutes": None,
    "healthcheck_interval_minutes": None,
    "snapshot_concurrent_workers": None,
    "snapshot_batch_size": None,
    "snapshot_batch_delay_seconds": None,
}


# ----------------------------
# Job Handlers
# ----------------------------
def run_snapshot(camera):
    with SessionLocal() as db:
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

                if not camera.id:
                    logger.error("Camera %s has no ID, skipping snapshot log insert.", camera.hostname)
                else:
                    snapshot_log = SnapshotLog(
                        id=str(uuid4()),
                        camera_id=camera.id,
                        camera_name=camera.hostname
                    )
                    db.add(snapshot_log)

                db.commit()
                logger.info("[SUCCESS] Scheduled snapshot for %s, saved with ID %s", camera.hostname, snapshot.id)
                return {"status": "success"}

            else:
                logger.warning("[FAIL] Snapshot failed for %s: %s", camera.hostname, result)
                return {"status": "error", "details": result}

        except Exception as e:
            logger.error("[ERROR] Snapshot failed for %s: %s", getattr(camera, "hostname", "unknown"), e)
            return {"status": "error", "details": str(e)}


def scheduled_snapshot():
    all_cameras = load_active_cameras()
    workers = int(get_config("snapshot_concurrent_workers", 5))

    logger.info(
        "[SCHEDULED] Running snapshot for %d cameras with %d workers (global pool).",
        len(all_cameras), workers
    )

    started_at = datetime.now(timezone.utc)
    time_start = monotonic()

    total_success_count = 0
    total_fail_count = 0

    BATCH_SIZE = int(get_config("snapshot_batch_size", 50))
    batch_delay = int(get_config("snapshot_batch_delay_seconds", 5))

    try:
        for i in range(0, len(all_cameras), BATCH_SIZE):
            current_batch = all_cameras[i:i + BATCH_SIZE]
            batch_number = int(i / BATCH_SIZE) + 1
            logger.info("[BATCH] Processing batch %d (%d cameras)", batch_number, len(current_batch))

            batch_success = 0
            batch_fail = 0

            futures = {thread_pool.submit(run_snapshot, cam): cam for cam in current_batch}

            for future in as_completed(futures):
                cam = futures[future]
                try:
                    result = future.result()
                    if result and result.get("status") == "success":
                        batch_success += 1
                        logger.info("[SUCCESS] Snapshot taken for: %s (Batch)", cam.hostname)
                    else:
                        batch_fail += 1
                        logger.warning("[FAIL] Snapshot failed or returned error for: %s (Batch)", cam.hostname)
                except Exception as e:
                    batch_fail += 1
                    logger.exception("[EXCEPTION] Unhandled error for %s (Batch): %s", cam.hostname, e)

            total_success_count += batch_success
            total_fail_count += batch_fail

            logger.info("[BATCH SUMMARY] Batch %d complete: %d succeeded, %d failed.", batch_number, batch_success, batch_fail)

            if i + BATCH_SIZE < len(all_cameras):
                logger.debug("Sleeping %d seconds before next batch", batch_delay)
                sleep(batch_delay)

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

        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        rss_mb = mem_info.rss / (1024 * 1024)
        vms_mb = mem_info.vms / (1024 * 1024)

        logger.info(
            "[SUMMARY] Snapshot run complete: %d succeeded, %d failed. Duration=%d ms",
            total_success_count, total_fail_count, duration_ms
        )
        logger.info(
            "[SUMMARY] Memory usage: RSS=%.2f MB, VMS=%.2f MB",
            rss_mb, vms_mb
        )

        with SessionLocal() as db:
            try:
                task_log = TaskTiming(
                    task_name="scheduled_snapshot",
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    status=status,
                )
                db.add(task_log)
                db.commit()
                logger.warning("[CONFIRM] TaskTiming committed to DB")
            except Exception as e:
                logger.exception("[TimingLog] Failed to save snapshot timing log: %s", e)


# ----------------------------
# Config Handlers
# ----------------------------
def handle_snapshot_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("scheduled_snapshot", trigger=IntervalTrigger(minutes=new_value))
        logger.info("[Scheduler] 🔄 Snapshot job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found for interval update.")


def handle_healthcheck_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("health_check", trigger=IntervalTrigger(minutes=new_value))
        logger.info("[Scheduler] 🔄 Healthcheck job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'health_check' not found for interval update.")


def handle_workers_update(scheduler, new_value):
    global thread_pool
    try:
        scheduler.modify_job("scheduled_snapshot", max_instances=new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found for workers update.")

    if thread_pool:
        thread_pool.shutdown(wait=False, cancel_futures=True)
    thread_pool = ThreadPoolExecutor(max_workers=new_value)
    logger.info("[Scheduler] 🔁 Thread pool recreated with %d workers", new_value)


def handle_batch_size(_, new_value):
    logger.info("[Scheduler] 🔁 Snapshot batch size updated to %d", new_value)


def handle_batch_delay(_, new_value):
    logger.info("[Scheduler] 🔁 Snapshot batch delay updated to %d seconds", new_value)


CONFIG_HANDLERS = {
    "snapshot_interval_minutes": handle_snapshot_interval,
    "healthcheck_interval_minutes": handle_healthcheck_interval,
    "snapshot_concurrent_workers": handle_workers_update,
    "snapshot_batch_size": handle_batch_size,
    "snapshot_batch_delay_seconds": handle_batch_delay,
}


# ----------------------------
# Scheduler Lifecycle
# ----------------------------
def start_scheduler():
    config = {
        "snapshot_interval_minutes": int(get_config("snapshot_interval_minutes", 600)),
        "healthcheck_interval_minutes": int(get_config("healthcheck_interval_minutes", 60)),
        "snapshot_concurrent_workers": int(get_config("snapshot_concurrent_workers", 5)),
        "snapshot_batch_size": int(get_config("snapshot_batch_size", 50)),
        "snapshot_batch_delay_seconds": int(get_config("snapshot_batch_delay_seconds", 5)),
    }

    last_config.update(config)

    scheduler.add_job(
        scheduled_snapshot,
        trigger=IntervalTrigger(minutes=config["snapshot_interval_minutes"]),
        id='scheduled_snapshot',
        max_instances=config["snapshot_concurrent_workers"],
        coalesce=True,
        misfire_grace_time=60
    )

    scheduler.add_job(
        ping_all_devices,
        trigger=IntervalTrigger(minutes=config["healthcheck_interval_minutes"]),
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
        new_config = {
            "snapshot_interval_minutes": int(get_config("snapshot_interval_minutes", 600)),
            "healthcheck_interval_minutes": int(get_config("healthcheck_interval_minutes", 60)),
            "snapshot_concurrent_workers": int(get_config("snapshot_concurrent_workers", 5)),
            "snapshot_batch_size": int(get_config("snapshot_batch_size", 50)),
            "snapshot_batch_delay_seconds": int(get_config("snapshot_batch_delay_seconds", 5)),
        }

        for key, new_value in new_config.items():
            old_value = last_config.get(key)
            if new_value != old_value:
                handler = CONFIG_HANDLERS.get(key)
                if handler:
                    handler(scheduler, new_value)
                last_config[key] = new_value
        
        # --- tambahan logging next run time ---
        for job_id in ["scheduled_snapshot", "health_check"]:
            job = scheduler.get_job(job_id)
            if job and job.next_run_time:
                logger.info("[Scheduler] ⏰ Next run for '%s' at %s", job_id, job.next_run_time)

        logger.info("[Scheduler] ✅ Scheduler config reloaded")

    except Exception as e:
        logger.warning("[Scheduler] ⚠️ Failed to reload scheduler config: %s", e)


# ----------------------------
# Cleanup Jobs
# ----------------------------
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
