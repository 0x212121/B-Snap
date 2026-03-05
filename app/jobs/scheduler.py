from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.base import JobLookupError
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.email_retry_queue import EmailRetryQueue
from app.models.log import ApiLog, CommandLog
from app.snapshot import load_active_cameras
from app.utils.email_notifier import send_recovery_alert, send_tamper_alert, send_offline_incident_email_once
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
# Lazy initialization - will be set in start_scheduler()
WORKERS = None
thread_pool = None

def init_thread_pool():
    global WORKERS, thread_pool
    if thread_pool is None:
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
    # Initialize thread pool if not already done
    init_thread_pool()
    
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
                    # Add 2 minute timeout per camera snapshot to prevent stuck workers
                    result = future.result(timeout=120)
                    if result and result.get("status") == "success":
                        batch_success += 1
                        logger.info("[SUCCESS] Snapshot taken for: %s (Batch)", cam.hostname)
                    else:
                        batch_fail += 1
                        logger.warning("[FAIL] Snapshot failed or returned error for: %s (Batch)", cam.hostname)
                except TimeoutError:
                    batch_fail += 1
                    logger.error("[TIMEOUT] Snapshot timed out for %s (Batch) after 300s", cam.hostname)
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
    # Initialize thread pool before starting scheduler
    init_thread_pool()
    
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

    scheduler.add_job(delete_old_api_logs, 'interval', days=1)
    scheduler.add_job(delete_old_command_logs, 'interval', days=1)

    scheduler.add_job(process_email_retry_queue, IntervalTrigger(minutes=1))
    scheduler.add_job(cleanup_email_retry_queue, 'interval', days=1)
    
    # Storage monitoring - every 1 hour
    scheduler.add_job(
        check_storage_job,
        IntervalTrigger(hours=1),
        id='storage_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300
    )


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
        retention_days = int(get_config("retention_audit_logs_days", 180))
        cutoff_date = date.today() - timedelta(days=retention_days)
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
        retention_days = int(get_config("retention_camera_stats_days", 90))
        cutoff_date = date.today() - timedelta(days=retention_days)
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


def delete_old_api_logs():
    db: Session = SessionLocal()
    try:
        retention_days = int(get_config("retention_api_logs_days", 90))
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted_rows = (
            db.query(ApiLog)
            .filter(ApiLog.timestamp < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("[delete_old_api_logs] Deleted %d rows older than %s", deleted_rows, cutoff_date.date())
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_api_logs] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def delete_old_command_logs():
    db: Session = SessionLocal()
    try:
        retention_days = int(get_config("retention_command_logs_days", 90))
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted_rows = (
            db.query(CommandLog)
            .filter(CommandLog.timestamp < cutoff_date)
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("[delete_old_command_logs] Deleted %d rows older than %s", deleted_rows, cutoff_date.date())
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_command_logs] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def check_storage_job():
    """
    Scheduled job to monitor storage usage.
    Runs every hour to track storage trends and alert on thresholds.
    """
    from app.utils.storage_monitor import record_storage_metric
    
    db: Session = SessionLocal()
    try:
        metric = record_storage_metric(db)
        logger.info(
            "[Storage Check] Level: %s, Usage: %.1f%%, Growth: %.2f GB/day",
            metric.alert_level,
            metric.usage_percent,
            metric.daily_growth_rate
        )
    except Exception as e:
        logger.error("[Storage Check] Error: %s", e, exc_info=True)
    finally:
        db.close()


def process_email_retry_queue():
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    # --- Parameter adaptif ---
    SCHEDULER_INTERVAL_MINUTES = 1      # scheduler kamu jalan tiap 1 menit
    BASE_RETRY_DELAY_MINUTES = 5        # minimal jeda antar attempt
    MAX_RETRY_DELAY_MINUTES = 60        # maksimum jeda antar attempt (1 jam)

    def _adaptive_delay(attempt: int) -> int:
        """Hitung delay adaptif (eksponensial, dibatasi ke 1 jam)."""
        delay = BASE_RETRY_DELAY_MINUTES * (2 ** (attempt - 1))
        return min(max(delay, SCHEDULER_INTERVAL_MINUTES), MAX_RETRY_DELAY_MINUTES)

    try:
        pending = (
            db.query(EmailRetryQueue)
            .filter(
                EmailRetryQueue.sent.is_(False),
                EmailRetryQueue.attempts < EmailRetryQueue.max_attempts,
                EmailRetryQueue.next_retry_at <= now,
            )
            # .with_for_update(skip_locked=True)  # aktifkan kalau multi-worker
            .order_by(EmailRetryQueue.created_at.asc())
            .all()
        )
    except Exception:
        db.close()
        raise

    if not pending:
        logger.debug("[RETRY] No pending emails at %s", now.strftime("%H:%M:%S"))
        db.close()
        return

    total = len(pending)
    success_count = 0
    fail_count = 0
    logger.info("[RETRY] Processing %d queued emails...", total)

    for task in pending:
        cam = db.query(Camera).filter(Camera.id == task.camera_id).first()
        if not cam:
            logger.warning("[RETRY] Camera %s not found, marking as sent", task.camera_id)
            task.sent = True
            db.commit()
            continue

        ok = False
        attempt_num = task.attempts + 1
        try:
            if task.type == "tamper":
                ok = bool(send_tamper_alert(db, cam, task.reason, task.file_path))
            elif task.type == "recovery":
                ok = bool(send_recovery_alert(db, cam))
            elif task.type == "offline":
                ok = bool(send_offline_incident_email_once(
                    db,
                    camera=cam,
                    incident_started_at=now - timedelta(minutes=30),
                    offline_duration_seconds=1800,
                ))
            elif task.type == "online":
                from app.utils.email_notifier import send_online_alert
                ok = bool(send_online_alert(db, cam))
            else:
                logger.warning("[RETRY] Unknown email type '%s' for %s", task.type, cam.hostname)
                task.sent = True
                db.commit()
                continue

            if ok:
                task.sent = True
                success_count += 1
                logger.info(
                    "[RETRY] Attempt %d/%d for %s (%s) – success",
                    attempt_num, task.max_attempts, cam.hostname, task.type
                )
            else:
                raise RuntimeError("Email send returned False")

        except Exception as e:
            # gagal → hitung delay adaptif
            task.attempts = attempt_num
            task.last_attempt = now
            delay_minutes = _adaptive_delay(task.attempts)
            task.next_retry_at = now + timedelta(minutes=delay_minutes)
            fail_count += 1

            logger.error(
                "[RETRY FAIL] Attempt %d/%d for %s (%s) – %s; next retry in %d min (at %s)",
                task.attempts, task.max_attempts, cam.hostname, task.type,
                e, delay_minutes, task.next_retry_at.strftime("%H:%M:%S"),
            )

            if task.attempts >= task.max_attempts:
                logger.error(
                    "[RETRY STOPPED] Max attempts reached for %s (%s) – giving up.",
                    cam.hostname, task.type
                )
        finally:
            db.commit()

    db.close()
    logger.info(
        "[RETRY SUMMARY] Finished processing %d tasks → success=%d, fail=%d",
        total, success_count, fail_count
    )


def cleanup_email_retry_queue():
    """Hapus antrean email retry yang sudah selesai >14 hari atau gagal total >7 hari."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    try:
        cutoff_success = now - timedelta(days=14)
        cutoff_fail = now - timedelta(days=7)

        # === Hapus antrean sukses lama (>14 hari) ===
        deleted_success = (
            db.query(EmailRetryQueue)
            .filter(
                EmailRetryQueue.sent.is_(True),
                EmailRetryQueue.last_attempt < cutoff_success,
            )
            .delete(synchronize_session=False)
        )

        # === Hapus antrean gagal total lama (>7 hari, attempts >= max_attempts) ===
        deleted_fail = (
            db.query(EmailRetryQueue)
            .filter(
                EmailRetryQueue.sent.is_(False),
                EmailRetryQueue.attempts >= EmailRetryQueue.max_attempts,
                EmailRetryQueue.last_attempt < cutoff_fail,
            )
            .delete(synchronize_session=False)
        )

        db.commit()

        total_deleted = (deleted_success or 0) + (deleted_fail or 0)
        if total_deleted > 0:
            logger.info(
                "[CLEANUP] Deleted %d old email retry rows (success>14d=%d, fail>7d=%d)",
                total_deleted, deleted_success or 0, deleted_fail or 0
            )
        else:
            logger.debug("[CLEANUP] No old email retry rows to delete")

    except Exception as e:
        db.rollback()
        logger.error("[CLEANUP ERROR] Failed to cleanup email retry queue: %s", e, exc_info=True)
    finally:
        db.close()
