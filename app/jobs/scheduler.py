from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from functools import wraps
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal, engine
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.email_retry_queue import EmailRetryQueue
from app.models.log import ApiLog, CommandLog
from app.snapshot import load_active_cameras
from app.utils.email_notifier import send_recovery_alert, send_tamper_alert, send_offline_incident_email_once
from app.utils.snapshot_locker import get_camera_lock
from app.utils.snapshot_service import take_snapshot
from app.utils.healthcheck import ping_all_devices
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.core.config import get_config
from app.models.audit_log import AuditLog
from app.models.camera_daily_stats import CameraDailyStats
from app.models.snapshot_log import SnapshotLog
from app.models.job_execution_log import JobExecutionLog
from app.utils.snapshot_utils import record_snapshot_metadata, check_orphaned_snapshots
from app.utils.orphaned_scanner import run_orphaned_scan
from sqlalchemy.orm import Session
from app.models.task_timing import TaskTiming
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from ping3 import ping
import psutil
import os
from time import monotonic, sleep

# --- Scheduler Init with Database Job Store ---
# Use SQLAlchemyJobStore so jobs are persisted and accessible from web app
jobstores = {
    'default': SQLAlchemyJobStore(engine=engine, tablename='apscheduler_jobs')
}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    job_defaults={
        "coalesce": True,
        "max_instances": 10,
        "misfire_grace_time": 60
    }
)

setup_logging()
logger = logging.getLogger("scheduler")

# --- Thread Pool ---
WORKERS = None
thread_pool = None

def init_thread_pool():
    global WORKERS, thread_pool
    if thread_pool is None:
        WORKERS = int(get_config("snapshot_concurrent_workers", 5))
        thread_pool = ThreadPoolExecutor(max_workers=WORKERS)


def create_trigger(cron_expr: str, default_trigger):
    """Create trigger from cron expression or return default trigger.
    
    Args:
        cron_expr: Cron expression string (e.g., "0 8,13,23 * * *")
        default_trigger: Fallback trigger if cron_expr is empty or invalid
        
    Returns:
        CronTrigger if cron_expr is valid, otherwise default_trigger
    """
    if not cron_expr or not cron_expr.strip():
        return default_trigger
    
    try:
        # Parse cron expression: minute hour day month weekday
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            logger.warning("[Scheduler] Invalid cron expression '%s', using default", cron_expr)
            return default_trigger
        
        minute, hour, day, month, day_of_week = parts
        
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week
        )
    except Exception as e:
        logger.warning("[Scheduler] Error parsing cron '%s': %s, using default", cron_expr, e)
        return default_trigger


# --- Global Config State ---
last_config = {
    "snapshot_interval_minutes": None,
    "healthcheck_interval_minutes": None,
    "snapshot_concurrent_workers": None,
    "snapshot_batch_size": None,
    "snapshot_batch_delay_seconds": None,
    "storage_check_interval_hours": None,
    "email_retry_interval_minutes": None,
    "cleanup_interval_days": None,
    "cleanup_retry_queue_interval_days": None,
    "wa_daily_report_hour": None,
    "wa_daily_report_minute": None,
    "wa_storage_alert_interval_hours": None,
    # Cron expressions
    "snapshot_cron": None,
    "healthcheck_cron": None,
    "storage_check_cron": None,
    "cleanup_cron": None,
    "email_retry_cron": None,
}


# --- Job Execution Decorator ---
def logged_job(job_id: str, job_name: str):
    """Decorator to log job execution to database.
    
    Args:
        job_id: Unique identifier for the job
        job_name: Human-readable job name
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            db = SessionLocal()
            log = None
            try:
                # Start execution log
                log = JobExecutionLog.start_execution(db, job_id, job_name)
                
                # Run the actual job
                result = func(*args, **kwargs)
                
                # Complete successfully
                records = getattr(result, 'records_processed', 0) if isinstance(result, dict) else 0
                log.complete(db, status="success", records=records)
                return result
                
            except Exception as e:
                logger.exception(f"[Job {job_id}] Failed: {e}")
                if log:
                    log.complete(db, status="fail", error=str(e))
                raise
            finally:
                db.close()
        return wrapper
    return decorator


# ----------------------------
# Job Handlers
# ----------------------------
def run_snapshot(camera):
    
    # --- SHORT CIRCUIT: Ping Check ---
    try:
        # Ambil config dengan type-safe conversion
        raw_config = get_config("snapshot_ping_check_enabled", "0")
        ping_enabled = str(raw_config).strip() == "1" if raw_config else False
        
        if ping_enabled:
            # Ambil timeout config
            timeout_raw = get_config("snapshot_ping_timeout_ms", "3000")
            try:
                timeout_sec = float(str(timeout_raw)) / 1000.0
                # Clamp antara 0.1s - 10s untuk safety
                timeout_sec = max(0.1, min(timeout_sec, 10.0))
            except (ValueError, TypeError):
                timeout_sec = 3.0  # Default fallback
            
            response_time = ping(camera.ip, timeout=int(timeout_sec), unit='s')
            
            if response_time is None or response_time is False:
                logger.warning(
                    "[SKIP] Camera %s (%s) unreachable (ping timeout: %.1fs)", 
                    camera.hostname, camera.ip, timeout_sec
                )
                return {
                    "status": "skipped", 
                    "reason": "host_unreachable",
                    "camera": camera.hostname,
                    "ping_time_ms": None
                }
                
            logger.debug(
                "[PING OK] Camera %s responded in %.2fms", 
                camera.hostname, response_time * 1000
            )
            
    except PermissionError:
        logger.error("[PING PERMISSION] Jalankan dengan sudo atau setcap cap_net_raw+ep $(which python)")
        # Lanjutkan tanpa pre-check jika permission denied
        pass
        
    except Exception as e:
        logger.warning("[PING ERROR] Camera %s: %s", camera.hostname, e)
        # Lanjutkan ke snapshot sebagai fallback (fail-open)
    
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


@logged_job("scheduled_snapshot", "Scheduled Snapshot")
def scheduled_snapshot():
    """Take snapshots from all active cameras."""
    init_thread_pool()
    
    all_cameras = load_active_cameras()
    workers = int(get_config("snapshot_concurrent_workers", 5))

    logger.info(
        "[SCHEDULED] Running snapshot for %d cameras with %d workers.",
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
                    result = future.result(timeout=120)
                    if result and result.get("status") == "success":
                        batch_success += 1
                    else:
                        batch_fail += 1
                except TimeoutError:
                    batch_fail += 1
                    logger.error("[TIMEOUT] Snapshot timed out for %s", cam.hostname)
                except Exception as e:
                    batch_fail += 1
                    logger.exception("[EXCEPTION] Error for %s: %s", cam.hostname, e)

            total_success_count += batch_success
            total_fail_count += batch_fail

            logger.info("[BATCH SUMMARY] Batch %d: %d succeeded, %d failed.", batch_number, batch_success, batch_fail)

            if i + BATCH_SIZE < len(all_cameras):
                sleep(batch_delay)

    except Exception as e:
        logger.exception("[SCHEDULER ERROR] Critical failure: %s", e)

    finally:
        duration_ms = int((monotonic() - time_start) * 1000)
        
        if total_success_count == 0 and total_fail_count == 0:
            status = "no_camera"
        elif total_success_count == 0:
            status = "fail"
        elif total_fail_count == 0:
            status = "success"
        else:
            status = "partial"

        logger.info(
            "[SUMMARY] Snapshot complete: %d succeeded, %d failed. Duration=%d ms",
            total_success_count, total_fail_count, duration_ms
        )

        # Also log to TaskTiming for backward compatibility
        with SessionLocal() as db:
            try:
                task_log = TaskTiming(
                    task_name="scheduled_snapshot",
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    duration_ms=duration_ms,
                    status=status,
                )
                db.add(task_log)
                db.commit()
            except Exception as e:
                logger.exception("[TimingLog] Failed: %s", e)
        
        # Return result for JobExecutionLog
        return {
            "records_processed": total_success_count,
            "total_failed": total_fail_count,
            "status": status
        }


# ----------------------------
# Config Handlers
# ----------------------------
def handle_snapshot_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("scheduled_snapshot", trigger=IntervalTrigger(minutes=new_value))
        logger.info("[Scheduler] Snapshot job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found.")


def handle_healthcheck_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("health_check", trigger=IntervalTrigger(minutes=new_value))
        logger.info("[Scheduler] Healthcheck job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'health_check' not found.")


def handle_workers_update(scheduler, new_value):
    global thread_pool
    try:
        scheduler.modify_job("scheduled_snapshot", max_instances=new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found.")

    if thread_pool:
        thread_pool.shutdown(wait=False, cancel_futures=True)
    thread_pool = ThreadPoolExecutor(max_workers=new_value)
    logger.info("[Scheduler] Thread pool recreated with %d workers", new_value)


def handle_batch_size(_, new_value):
    logger.info("[Scheduler] Snapshot batch size updated to %d", new_value)


def handle_batch_delay(_, new_value):
    logger.info("[Scheduler] Snapshot batch delay updated to %d seconds", new_value)


def handle_storage_check_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("storage_check", trigger=IntervalTrigger(hours=new_value))
        logger.info("[Scheduler] Storage check interval updated to %d hours", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'storage_check' not found.")


def handle_email_retry_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("email_retry", trigger=IntervalTrigger(minutes=new_value))
        logger.info("[Scheduler] Email retry interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'email_retry' not found.")


def handle_cleanup_interval(scheduler, new_value):
    jobs = ['cleanup_audit_logs', 'cleanup_camera_stats', 'cleanup_api_logs', 'cleanup_command_logs']
    for job_id in jobs:
        try:
            scheduler.reschedule_job(job_id, trigger=IntervalTrigger(days=new_value))
        except JobLookupError:
            pass
    logger.info("[Scheduler] Cleanup jobs interval updated to %d days", new_value)


def handle_wa_daily_report_time(scheduler, hour, minute):
    try:
        scheduler.reschedule_job("wa_daily_report", trigger=CronTrigger(hour=hour, minute=minute))
        logger.info("[Scheduler] WA daily report time updated to %02d:%02d", hour, minute)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'wa_daily_report' not found.")


def handle_wa_storage_alert_interval(scheduler, new_value):
    try:
        scheduler.reschedule_job("wa_storage_alert", trigger=IntervalTrigger(hours=new_value))
        logger.info("[Scheduler] WA storage alert interval updated to %d hours", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'wa_storage_alert' not found.")


CONFIG_HANDLERS = {
    "snapshot_interval_minutes": handle_snapshot_interval,
    "healthcheck_interval_minutes": handle_healthcheck_interval,
    "snapshot_concurrent_workers": handle_workers_update,
    "snapshot_batch_size": handle_batch_size,
    "snapshot_batch_delay_seconds": handle_batch_delay,
    "storage_check_interval_hours": handle_storage_check_interval,
    "email_retry_interval_minutes": handle_email_retry_interval,
    "cleanup_interval_days": handle_cleanup_interval,
}


# ----------------------------
# Scheduler Lifecycle
# ----------------------------
def start_scheduler():
    init_thread_pool()
    
    # Clear apscheduler_jobs table to prevent duplicate key errors
    # This is necessary when the scheduler container restarts
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM apscheduler_jobs"))
            conn.commit()
            logger.info("[Scheduler] Cleared apscheduler_jobs table")
    except Exception as e:
        logger.warning("[Scheduler] Could not clear apscheduler_jobs table: %s", e)
    
    # Load all config with new interval settings and cron expressions
    config = {
        "snapshot_interval_minutes": int(get_config("snapshot_interval_minutes", 480)),
        "healthcheck_interval_minutes": int(get_config("healthcheck_interval_minutes", 15)),
        "snapshot_concurrent_workers": int(get_config("snapshot_concurrent_workers", 5)),
        "snapshot_batch_size": int(get_config("snapshot_batch_size", 50)),
        "snapshot_batch_delay_seconds": int(get_config("snapshot_batch_delay_seconds", 5)),
        "storage_check_interval_hours": int(get_config("storage_check_interval_hours", 1)),
        "email_retry_interval_minutes": int(get_config("email_retry_interval_minutes", 1)),
        "cleanup_interval_days": int(get_config("cleanup_interval_days", 1)),
        "cleanup_retry_queue_interval_days": int(get_config("cleanup_retry_queue_interval_days", 1)),
        "wa_daily_report_hour": int(get_config("wa_daily_report_hour", 8)),
        "wa_daily_report_minute": int(get_config("wa_daily_report_minute", 0)),
        "wa_storage_alert_interval_hours": int(get_config("wa_storage_alert_interval_hours", 2)),
        # Cron expressions
        "snapshot_cron": get_config("snapshot_cron", ""),
        "healthcheck_cron": get_config("healthcheck_cron", ""),
        "storage_check_cron": get_config("storage_check_cron", ""),
        "cleanup_cron": get_config("cleanup_cron", "0 2 * * *"),
        "email_retry_cron": get_config("email_retry_cron", ""),
    }

    last_config.update(config)

    # Main jobs with cron support
    snapshot_trigger = create_trigger(
        config["snapshot_cron"],
        IntervalTrigger(minutes=config["snapshot_interval_minutes"])
    )
    scheduler.add_job(
        scheduled_snapshot,
        trigger=snapshot_trigger,
        id='scheduled_snapshot',
        max_instances=config["snapshot_concurrent_workers"],
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True
    )

    healthcheck_trigger = create_trigger(
        config["healthcheck_cron"],
        IntervalTrigger(minutes=config["healthcheck_interval_minutes"])
    )
    scheduler.add_job(
        logged_job("health_check", "Health Check")(ping_all_devices),
        trigger=healthcheck_trigger,
        id='health_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True
    )

    # Cleanup jobs with cron support (default: 2 AM daily)
    cleanup_trigger = create_trigger(
        config["cleanup_cron"],
        IntervalTrigger(days=config["cleanup_interval_days"])
    )
    cleanup_jobs = [
        ('cleanup_audit_logs', delete_old_audit_logs),
        ('cleanup_camera_stats', delete_old_camera_stats),
        ('cleanup_api_logs', delete_old_api_logs),
        ('cleanup_command_logs', delete_old_command_logs),
    ]
    
    for job_id, func in cleanup_jobs:
        scheduler.add_job(
            logged_job(job_id, job_id.replace('_', ' ').title())(func),
            trigger=cleanup_trigger,
            id=job_id,
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )

    # Email retry with cron support
    email_retry_trigger = create_trigger(
        config["email_retry_cron"],
        IntervalTrigger(minutes=config["email_retry_interval_minutes"])
    )
    scheduler.add_job(
        logged_job("email_retry", "Email Retry")(process_email_retry_queue),
        trigger=email_retry_trigger,
        id='email_retry',
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )
    
    scheduler.add_job(
        logged_job("cleanup_email_retry", "Email Retry Cleanup")(cleanup_email_retry_queue),
        trigger=IntervalTrigger(days=config["cleanup_retry_queue_interval_days"]),
        id='cleanup_email_retry',
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )
    
    # Storage monitoring with cron support
    storage_trigger = create_trigger(
        config["storage_check_cron"],
        IntervalTrigger(hours=config["storage_check_interval_hours"])
    )
    scheduler.add_job(
        logged_job("storage_check", "Storage Check")(check_storage_job),
        trigger=storage_trigger,
        id='storage_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )
    
    # WhatsApp daily report with configurable time
    scheduler.add_job(
        logged_job("wa_daily_report", "WhatsApp Daily Report")(send_wa_camera_no_snapshot_report),
        trigger=CronTrigger(hour=config["wa_daily_report_hour"], minute=config["wa_daily_report_minute"]),
        id='wa_daily_report',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )
    
    # WhatsApp storage alert with configurable interval
    scheduler.add_job(
        logged_job("wa_storage_alert", "WhatsApp Storage Alert")(send_wa_storage_alert),
        trigger=IntervalTrigger(hours=config["wa_storage_alert_interval_hours"]),
        id='wa_storage_alert',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )
    
    # Orphaned snapshots check - runs every hour
    scheduler.add_job(
        logged_job("orphaned_snapshots_check", "Orphaned Snapshots Check")(check_orphaned_snapshots_job),
        trigger=IntervalTrigger(hours=1),
        id='orphaned_snapshots_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )
    
    # Orphaned files folder scan - runs daily at 3 AM
    scheduler.add_job(
        logged_job("orphaned_files_scan", "Orphaned Files Scan")(scan_orphaned_files_job),
        trigger=CronTrigger(hour=3, minute=0),
        id='orphaned_files_scan',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True
    )

    scheduler.start()
    logger.info("[Scheduler] Started with %d jobs", len(scheduler.get_jobs()))
    return scheduler


def update_scheduler_config():
    try:
        new_config = {
            "snapshot_interval_minutes": int(get_config("snapshot_interval_minutes", 480)),
            "healthcheck_interval_minutes": int(get_config("healthcheck_interval_minutes", 15)),
            "snapshot_concurrent_workers": int(get_config("snapshot_concurrent_workers", 5)),
            "snapshot_batch_size": int(get_config("snapshot_batch_size", 50)),
            "snapshot_batch_delay_seconds": int(get_config("snapshot_batch_delay_seconds", 5)),
            "storage_check_interval_hours": int(get_config("storage_check_interval_hours", 1)),
            "email_retry_interval_minutes": int(get_config("email_retry_interval_minutes", 1)),
            "cleanup_interval_days": int(get_config("cleanup_interval_days", 1)),
            "wa_storage_alert_interval_hours": int(get_config("wa_storage_alert_interval_hours", 2)),
            # Cron expressions
            "snapshot_cron": get_config("snapshot_cron", ""),
            "healthcheck_cron": get_config("healthcheck_cron", ""),
            "storage_check_cron": get_config("storage_check_cron", ""),
            "cleanup_cron": get_config("cleanup_cron", "0 2 * * *"),
            "email_retry_cron": get_config("email_retry_cron", ""),
        }

        # Check for cron expression changes
        cron_jobs = {
            'snapshot_cron': 'scheduled_snapshot',
            'healthcheck_cron': 'health_check',
            'storage_check_cron': 'storage_check',
            'cleanup_cron': 'cleanup_audit_logs',  # All cleanup jobs use same trigger
            'email_retry_cron': 'email_retry',
        }
        
        for cron_key, job_id in cron_jobs.items():
            new_cron = new_config.get(cron_key, "")
            old_cron = last_config.get(cron_key, "")
            if new_cron != old_cron:
                # Reschedule job with new trigger
                job = scheduler.get_job(job_id)
                if job:
                    # Get the appropriate default trigger
                    if cron_key == 'snapshot_cron':
                        default_trigger = IntervalTrigger(minutes=new_config["snapshot_interval_minutes"])
                    elif cron_key == 'healthcheck_cron':
                        default_trigger = IntervalTrigger(minutes=new_config["healthcheck_interval_minutes"])
                    elif cron_key == 'storage_check_cron':
                        default_trigger = IntervalTrigger(hours=new_config["storage_check_interval_hours"])
                    elif cron_key == 'cleanup_cron':
                        default_trigger = IntervalTrigger(days=new_config["cleanup_interval_days"])
                    elif cron_key == 'email_retry_cron':
                        default_trigger = IntervalTrigger(minutes=new_config["email_retry_interval_minutes"])
                    else:
                        continue
                    
                    new_trigger = create_trigger(new_cron, default_trigger)
                    job.reschedule(trigger=new_trigger)
                    logger.info("[Scheduler] Rescheduled %s with trigger: %s", job_id, new_trigger)
                last_config[cron_key] = new_cron

        # Handle regular interval changes
        for key in ["snapshot_interval_minutes", "healthcheck_interval_minutes", 
                    "storage_check_interval_hours", "email_retry_interval_minutes",
                    "cleanup_interval_days", "wa_storage_alert_interval_hours"]:
            new_value = new_config[key]
            old_value = last_config.get(key)
            if new_value != old_value:
                handler = CONFIG_HANDLERS.get(key)
                if handler:
                    handler(scheduler, new_value)
                last_config[key] = new_value
        
        # Handle WA daily report time separately (needs both hour and minute)
        new_hour = int(get_config("wa_daily_report_hour", 8))
        new_minute = int(get_config("wa_daily_report_minute", 0))
        if (new_hour != last_config.get("wa_daily_report_hour") or 
            new_minute != last_config.get("wa_daily_report_minute")):
            handle_wa_daily_report_time(scheduler, new_hour, new_minute)
            last_config["wa_daily_report_hour"] = new_hour
            last_config["wa_daily_report_minute"] = new_minute
        
        # Log next run times
        for job in scheduler.get_jobs():
            if job and job.next_run_time:
                logger.info("[Scheduler] Next run '%s' at %s", job.id, job.next_run_time)

        logger.info("[Scheduler] Config reloaded")

    except Exception as e:
        logger.warning("[Scheduler] Failed to reload config: %s", e)


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
        return {"records_processed": deleted_rows}
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_audit_logs] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


# ----------------------------
# Orphaned Snapshots Job
# ----------------------------
def check_orphaned_snapshots_job():
    """Job to check and mark orphaned snapshots."""
    db: Session = SessionLocal()
    try:
        newly_orphaned, total_orphaned = check_orphaned_snapshots(db)
        logger.info(
            "[Orphaned Check] Newly orphaned: %d, Total orphaned: %d",
            newly_orphaned, total_orphaned
        )
        return {
            "records_processed": newly_orphaned,
            "total_orphaned": total_orphaned
        }
    except Exception as e:
        logger.error("[Orphaned Check] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def scan_orphaned_files_job():
    """Job to scan filesystem for orphaned snapshot files."""
    db: Session = SessionLocal()
    try:
        result = run_orphaned_scan(db)
        logger.info(
            "[Orphaned Scan] Disk: %d files, DB: %d records, Found: %d orphaned (%d new)",
            result['disk_files'],
            result['db_records'],
            result['orphaned_found'],
            result['new_records']
        )
        return {
            "records_processed": result['new_records'],
            "orphaned_found": result['orphaned_found'],
            "total_orphaned": result['total_orphaned']
        }
    except Exception as e:
        logger.error("[Orphaned Scan] Error: %s", e, exc_info=True)
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
        return {"records_processed": deleted_rows}
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
        return {"records_processed": deleted_rows}
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
        return {"records_processed": deleted_rows}
    except Exception as e:
        db.rollback()
        logger.error("[delete_old_command_logs] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def check_storage_job():
    """Monitor storage usage."""
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
        return {"records_processed": 1, "alert_level": metric.alert_level}
    except Exception as e:
        logger.error("[Storage Check] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def process_email_retry_queue():
    """Process queued emails with adaptive retry."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    SCHEDULER_INTERVAL_MINUTES = 1
    BASE_RETRY_DELAY_MINUTES = 5
    MAX_RETRY_DELAY_MINUTES = 60

    def _adaptive_delay(attempt: int) -> int:
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
            .order_by(EmailRetryQueue.created_at.asc())
            .all()
        )
    except Exception:
        db.close()
        raise

    if not pending:
        db.close()
        return {"records_processed": 0}

    total = len(pending)
    success_count = 0
    fail_count = 0
    logger.info("[RETRY] Processing %d queued emails...", total)

    for task in pending:
        cam = db.query(Camera).filter(Camera.id == task.camera_id).first()
        if not cam:
            logger.warning("[RETRY] Camera %s not found", task.camera_id)
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
                    db, camera=cam,
                    incident_started_at=now - timedelta(minutes=30),
                    offline_duration_seconds=1800,
                ))
            elif task.type == "online":
                from app.utils.email_notifier import send_online_alert
                ok = bool(send_online_alert(db, cam))
            else:
                task.sent = True
                db.commit()
                continue

            if ok:
                task.sent = True
                success_count += 1
            else:
                raise RuntimeError("Email send returned False")

        except Exception as e:
            task.attempts = attempt_num
            task.last_attempt = now
            delay_minutes = _adaptive_delay(task.attempts)
            task.next_retry_at = now + timedelta(minutes=delay_minutes)
            fail_count += 1

            logger.error(
                "[RETRY FAIL] Attempt %d/%d for %s: %s; next retry in %d min",
                task.attempts, task.max_attempts, cam.hostname, e, delay_minutes
            )

            if task.attempts >= task.max_attempts:
                logger.error("[RETRY STOPPED] Max attempts for %s", cam.hostname)
        finally:
            db.commit()

    db.close()
    logger.info("[RETRY SUMMARY] %d tasks: success=%d, fail=%d", total, success_count, fail_count)
    return {"records_processed": success_count}


def cleanup_email_retry_queue():
    """Delete old completed/failed email retries."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)

    try:
        cutoff_success = now - timedelta(days=14)
        cutoff_fail = now - timedelta(days=7)

        deleted_success = (
            db.query(EmailRetryQueue)
            .filter(
                EmailRetryQueue.sent.is_(True),
                EmailRetryQueue.last_attempt < cutoff_success,
            )
            .delete(synchronize_session=False)
        )

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
        total = (deleted_success or 0) + (deleted_fail or 0)
        if total > 0:
            logger.info("[CLEANUP] Deleted %d email retry rows", total)
        return {"records_processed": total}
    except Exception as e:
        db.rollback()
        logger.error("[CLEANUP ERROR] %s", e, exc_info=True)
        raise
    finally:
        db.close()


# ----------------------------
# WhatsApp Report Jobs
# ----------------------------
def send_wa_camera_no_snapshot_report():
    """Send WhatsApp report for cameras without recent snapshots."""
    db: Session = SessionLocal()
    try:
        wa_service = WAGatewayService(db)
        
        if not wa_service.config.is_configured():
            return {"records_processed": 0}
        
        receiver = wa_service.config.default_receiver
        if not receiver:
            return {"records_processed": 0}
        
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        cameras = db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"])).all()
        
        no_snapshot_cameras = []
        for cam in cameras:
            recent_snapshot = (
                db.query(SnapshotLog)
                .filter(SnapshotLog.camera_id == cam.id)
                .filter(SnapshotLog.created_at >= yesterday)
                .first()
            )
            if not recent_snapshot:
                no_snapshot_cameras.append(cam)
        
        unhealthy = (
            db.query(CameraHealth)
            .filter(CameraHealth.status != "healthy")
            .all()
        )
        
        if not no_snapshot_cameras and not unhealthy:
            return {"records_processed": 0}
        
        lines = ["📊 *B-SNAP Daily Camera Status Report*\n"]
        lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        
        if no_snapshot_cameras:
            lines.append(f"⚠️ *Cameras without snapshots:* {len(no_snapshot_cameras)}")
            for cam in no_snapshot_cameras[:10]:
                lines.append(f"• {cam.name}")
            lines.append("")
        
        message = "\n".join(lines)
        
        receivers = [r.strip() for r in receiver.split(",") if r.strip()]
        sent_count = 0
        for phone in receivers:
            phone = format_phone_number(phone)
            result = wa_service.send_text(phone, message)
            if result["success"]:
                sent_count += 1
        
        return {"records_processed": sent_count}
    except Exception as e:
        logger.error("[WA Report] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def send_wa_storage_alert():
    """Send WhatsApp alert when storage is critical."""
    from app.utils.storage_monitor import get_disk_usage, check_storage_thresholds
    from app.models.storage_metric import StorageAlert
    
    db: Session = SessionLocal()
    try:
        wa_service = WAGatewayService(db)
        
        if not wa_service.config.is_configured():
            return {"records_processed": 0}
        
        receiver = wa_service.config.default_receiver
        if not receiver:
            return {"records_processed": 0}
        
        total, used, free, percent = get_disk_usage("/")
        free_gb = free / (1024**3)
        
        alert_level, alert_msg = check_storage_thresholds(percent, free_gb, db)
        
        if alert_level not in ["warning", "critical"]:
            return {"records_processed": 0}
        
        recent_alert = (
            db.query(StorageAlert)
            .filter(StorageAlert.level == alert_level)
            .filter(StorageAlert.resolved_at.is_(None))
            .first()
        )
        
        if not recent_alert:
            return {"records_processed": 0}
        
        emoji = "🔴" if alert_level == "critical" else "🟡"
        message = f"""{emoji} *B-SNAP Storage Alert*

{alert_msg}

💾 Disk Usage: {percent:.1f}%
🆓 Free Space: {free_gb:.1f} GB
"""
        
        receivers = [r.strip() for r in receiver.split(",") if r.strip()]
        sent_count = 0
        for phone in receivers:
            phone = format_phone_number(phone)
            result = wa_service.send_text(phone, message)
            if result["success"]:
                sent_count += 1
        
        return {"records_processed": sent_count}
    except Exception as e:
        logger.error("[WA Storage Alert] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()
