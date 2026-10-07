from __future__ import annotations

from copy import copy
from datetime import datetime, timedelta, timezone, tzinfo
from uuid import uuid4
from functools import wraps
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.util import astimezone as scheduler_timezone
from app.core.logging_config import setup_logging
from app.db.database import SessionLocal, engine
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.email_retry_queue import EmailRetryQueue
from app.models.log import ApiLog, CommandLog
from app.utils.timezone_helper import get_current_timezone, to_current_timezone
from app.snapshot import load_active_cameras
from app.utils.email_notifier import (
    send_recovery_alert,  # For tampered -> normal transitions
    send_tamper_alert,     # For tamper detection
    send_offline_incident_email_once  # For offline alerts (online alerts intentionally disabled)
)
from app.utils.snapshot_locker import get_camera_lock
from app.utils.snapshot_process import run_camera_process
from app.utils.snapshot_service import take_snapshot
from app.utils.healthcheck import ping_all_devices
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.core.config import get_config
from app.core.job_schedules import JOB_SCHEDULES, get_job_schedule, build_job_trigger
from app.models.audit_log import AuditLog
from app.models.camera_daily_stats import CameraDailyStats
from app.models.snapshot_log import SnapshotLog
from app.models.job_execution_log import JobExecutionLog
from app.utils.snapshot_utils import record_snapshot_metadata, check_orphaned_snapshots
from app.utils.check_stats import check_stats
from app.utils.record_check import cleanup_old_record_checks, run_all_record_checks
from app.utils.record_check_report import send_record_check_daily_reports
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
    timezone=timezone.utc,
    jobstores=jobstores,
    job_defaults={
        "coalesce": True,
        "max_instances": 10,
        "misfire_grace_time": 60
    }
)

setup_logging()
logger = logging.getLogger("scheduler")


def get_scheduler_timezone() -> tzinfo:
    """Read the configured timezone, falling back to UTC for invalid settings."""
    db = SessionLocal()
    try:
        return scheduler_timezone(get_current_timezone(db))
    finally:
        db.close()


def update_scheduler_timezone() -> None:
    """Apply timezone changes to all existing cron and interval jobs."""
    configured_timezone = scheduler_timezone(get_scheduler_timezone())
    timezone_name = str(configured_timezone)
    if timezone_name == last_config.get("timezone"):
        return

    scheduler.timezone = configured_timezone
    for job in scheduler.get_jobs():
        if isinstance(job.trigger, (CronTrigger, IntervalTrigger)):
            trigger = copy(job.trigger)
            trigger.timezone = configured_timezone
            job.reschedule(trigger=trigger)
    last_config["timezone"] = timezone_name
    logger.info("[Scheduler] Timezone updated to %s", timezone_name)


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
            day_of_week=day_of_week,
            timezone=scheduler.timezone,
        )
    except Exception as e:
        logger.warning("[Scheduler] Error parsing cron '%s': %s, using default", cron_expr, e)
        return default_trigger


# --- Global Config State ---
last_config = {
    "timezone": None,
    "snapshot_interval_minutes": None,
    "healthcheck_interval_minutes": None,
    "snapshot_concurrent_workers": None,
    "snapshot_batch_size": None,
    "snapshot_batch_delay_seconds": None,
    "storage_check_interval_hours": None,
    "record_check_interval_minutes": None,
    "email_retry_interval_minutes": None,
    "cleanup_interval_days": None,
    "cleanup_retry_queue_interval_days": None,
    "wa_daily_report_hour": None,
    "wa_daily_report_minute": None,
    # Cron expressions
    "snapshot_cron": None,
    "healthcheck_cron": None,
    "storage_check_cron": None,
    "record_check_cron": None,
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
                records = result.get('records_processed', 0) if isinstance(result, dict) else 0
                metadata = result if job_id == "scheduled_snapshot" and isinstance(result, dict) else None
                status = metadata.get("status", "success") if metadata else "success"
                log.complete(db, status=status, records=records, metadata=metadata)
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

# Rate limiting untuk snapshot per kamera (anti-flooding)
# Format: {camera_id: last_snapshot_timestamp}
_snapshot_rate_limit_cache = {}
MIN_SNAPSHOT_INTERVAL_SECONDS = 30  # Minimum 30 detik antar snapshot untuk kamera yang sama

def run_snapshot(camera: Camera) -> dict:
    """Hold the camera lock through capture, metadata and health updates."""
    try:
        with get_camera_lock(str(camera.id)):
            return _run_snapshot(camera)
    except Exception:
        logger.exception("[ERROR] Snapshot worker failed for camera %s", camera.id)
        return {"status": "error"}


def _run_snapshot(camera: Camera) -> dict:
    
    # --- RATE LIMITING: Cek apakah kamera baru saja di-snapshot ---
    now = datetime.now(timezone.utc)
    camera_id = str(camera.id)
    
    if camera_id in _snapshot_rate_limit_cache:
        last_snapshot = _snapshot_rate_limit_cache[camera_id]
        elapsed = (now - last_snapshot).total_seconds()
        
        if elapsed < MIN_SNAPSHOT_INTERVAL_SECONDS:
            logger.debug(
                "[RATE LIMIT] Skipping snapshot for %s - last snapshot %.1f seconds ago (min: %d)",
                camera.hostname, elapsed, MIN_SNAPSHOT_INTERVAL_SECONDS
            )
            return {
                "status": "skipped",
                "reason": "rate_limited",
                "camera": camera.hostname,
                "retry_after": MIN_SNAPSHOT_INTERVAL_SECONDS - int(elapsed)
            }
    
    # Update cache
    _snapshot_rate_limit_cache[camera_id] = now
    
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
                
                # Update camera daily stats
                check_stats(camera)
                
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
    all_cameras = load_active_cameras()
    workers = max(1, min(32, int(get_config("snapshot_concurrent_workers", 5))))

    logger.info(
        "[SCHEDULED] Running snapshot for %d cameras with %d workers.",
        len(all_cameras), workers
    )

    started_at = datetime.now(timezone.utc)
    time_start = monotonic()

    total_success_count = 0
    total_fail_count = 0
    total_timeout_count = 0
    try:
        camera_timeout = max(10, min(600, int(os.environ.get("SNAPSHOT_JOB_CAMERA_TIMEOUT", "90"))))
    except ValueError:
        camera_timeout = 90

    BATCH_SIZE = max(1, int(get_config("snapshot_batch_size", 50)))
    batch_delay = max(0, int(get_config("snapshot_batch_delay_seconds", 5)))
    fatal_error = None
    # Config reload applies to the next run and cannot cancel queued cameras.
    thread_pool = ThreadPoolExecutor(max_workers=workers)

    try:
        for i in range(0, len(all_cameras), BATCH_SIZE):
            current_batch = all_cameras[i:i + BATCH_SIZE]
            batch_number = int(i / BATCH_SIZE) + 1
            logger.info("[BATCH] Processing batch %d (%d cameras)", batch_number, len(current_batch))

            batch_success = 0
            batch_fail = 0

            # Deadlines start on execution, not while waiting in the queue.
            futures = {
                thread_pool.submit(run_camera_process, str(cam.id), camera_timeout): cam
                for cam in current_batch
            }

            for future in as_completed(futures):
                cam = futures[future]
                try:
                    result = future.result()
                    if result and result.get("status") == "timeout":
                        total_timeout_count += 1
                        logger.error("[TIMEOUT] Camera %s (%s) exceeded %ss; worker terminated",
                                     cam.hostname, cam.id, camera_timeout)
                    if result and result.get("status") == "success":
                        batch_success += 1
                    else:
                        batch_fail += 1
                        logger.warning("[CAMERA RESULT] %s (%s): %s", cam.hostname, cam.id, result)
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
        fatal_error = str(e)
        logger.exception("[SCHEDULER ERROR] Critical failure: %s", e)

    finally:
        thread_pool.shutdown(wait=True, cancel_futures=True)
        duration_ms = int((monotonic() - time_start) * 1000)
        
        if fatal_error:
            status = "fail"
        elif total_success_count == 0 and total_fail_count == 0:
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
        
    # Do not suppress cancellation or unexpected exceptions by returning in finally.
    return {
        "records_processed": total_success_count,
        "total_failed": total_fail_count,
        "timed_out": total_timeout_count,
        "status": status,
        "error": fatal_error,
    }


# ----------------------------
# Config Handlers
# ----------------------------
def handle_snapshot_interval(scheduler, new_value):
    # Check if cron expression is set - if so, don't override with interval
    cron_expr = get_config("snapshot_cron", "").strip()
    if cron_expr:
        logger.info(
            "[Scheduler] Snapshot job has cron expression '%s', skipping interval update", cron_expr
        )
        return
    try:
        scheduler.reschedule_job(
            "scheduled_snapshot",
            trigger=IntervalTrigger(minutes=new_value, timezone=scheduler.timezone),
        )
        logger.info("[Scheduler] Snapshot job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found.")


def handle_healthcheck_interval(scheduler, new_value):
    # Check if cron expression is set - if so, don't override with interval
    cron_expr = get_config("healthcheck_cron", "").strip()
    if cron_expr:
        logger.info(
            "[Scheduler] Healthcheck job has cron expression '%s', skipping interval update",
            cron_expr,
        )
        return
    try:
        scheduler.reschedule_job(
            "health_check", trigger=IntervalTrigger(minutes=new_value, timezone=scheduler.timezone)
        )
        logger.info("[Scheduler] Healthcheck job interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'health_check' not found.")


def handle_workers_update(scheduler, new_value):
    try:
        scheduler.modify_job("scheduled_snapshot", max_instances=1)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'scheduled_snapshot' not found.")

    logger.info("[Scheduler] Next snapshot run will use %d workers", new_value)


def handle_batch_size(_, new_value):
    logger.info("[Scheduler] Snapshot batch size updated to %d", new_value)


def handle_batch_delay(_, new_value):
    logger.info("[Scheduler] Snapshot batch delay updated to %d seconds", new_value)


def handle_storage_check_interval(scheduler, new_value):
    # Check if cron expression is set - if so, don't override with interval
    cron_expr = get_config("storage_check_cron", "").strip()
    if cron_expr:
        logger.info(
            "[Scheduler] Storage check job has cron expression '%s', skipping interval update",
            cron_expr,
        )
        return
    try:
        scheduler.reschedule_job(
            "storage_check", trigger=IntervalTrigger(hours=new_value, timezone=scheduler.timezone)
        )
        logger.info("[Scheduler] Storage check interval updated to %d hours", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'storage_check' not found.")


def handle_record_check_interval(scheduler, new_value):
    cron_expr = get_config("record_check_cron", "").strip()
    if cron_expr:
        logger.info(
            "[Scheduler] Record check job has cron expression '%s', skipping interval update",
            cron_expr,
        )
        return
    try:
        scheduler.reschedule_job(
            "record_folder_check",
            trigger=IntervalTrigger(minutes=new_value, timezone=scheduler.timezone),
        )
        logger.info("[Scheduler] Record check interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'record_folder_check' not found.")


def handle_email_retry_interval(scheduler, new_value):
    # Check if cron expression is set - if so, don't override with interval
    cron_expr = get_config("email_retry_cron", "").strip()
    if cron_expr:
        # Cron takes precedence, only update the fallback default
        logger.info(
            "[Scheduler] Email retry has cron expression '%s', skipping interval update", cron_expr
        )
        return
    try:
        scheduler.reschedule_job(
            "email_retry", trigger=IntervalTrigger(minutes=new_value, timezone=scheduler.timezone)
        )
        logger.info("[Scheduler] Email retry interval updated to %d minutes", new_value)
    except JobLookupError:
        logger.warning("[Scheduler] Job 'email_retry' not found.")


def handle_cleanup_interval(scheduler, new_value):
    # Check if cron expression is set - if so, don't override with interval
    cron_expr = get_config("cleanup_cron", "").strip()
    if cron_expr:
        logger.info("[Scheduler] Cleanup jobs have cron expression '%s', skipping interval update", cron_expr)
        return
    jobs = ["cleanup_camera_stats", "cleanup_api_logs", "cleanup_command_logs"]
    for job_id in jobs:
        try:
            scheduler.reschedule_job(
                job_id, trigger=IntervalTrigger(days=new_value, timezone=scheduler.timezone)
            )
        except JobLookupError:
            pass
    logger.info("[Scheduler] Cleanup jobs interval updated to %d days", new_value)


def handle_wa_daily_report_time(scheduler, hour, minute):
    for job_id in ("wa_daily_report", "record_check_daily_report"):
        try:
            scheduler.reschedule_job(
                job_id,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=scheduler.timezone),
            )
        except JobLookupError:
            logger.warning("[Scheduler] Job '%s' not found.", job_id)
    logger.info("[Scheduler] Daily report time updated to %02d:%02d", hour, minute)


CONFIG_HANDLERS = {
    "snapshot_interval_minutes": handle_snapshot_interval,
    "healthcheck_interval_minutes": handle_healthcheck_interval,
    "snapshot_concurrent_workers": handle_workers_update,
    "snapshot_batch_size": handle_batch_size,
    "snapshot_batch_delay_seconds": handle_batch_delay,
    "storage_check_interval_hours": handle_storage_check_interval,
    "record_check_interval_minutes": handle_record_check_interval,
    "email_retry_interval_minutes": handle_email_retry_interval,
    "cleanup_interval_days": handle_cleanup_interval,
}


# ----------------------------
# Scheduler Lifecycle
# ----------------------------
def start_scheduler():

    # Ensure APScheduler tables exist before starting scheduler
    # This prevents errors when the table doesn't exist yet (first run or fresh database)
    try:
        from sqlalchemy import inspect, text

        inspector = inspect(engine)
        if not inspector.has_table('apscheduler_jobs'):
            logger.info("[Scheduler] Creating APScheduler tables...")
            # Create the table using SQLAlchemy's create_all via the jobstore's metadata
            # This ensures compatibility with different database backends
            from sqlalchemy import Column, String, Float, LargeBinary, Index, MetaData
            from sqlalchemy import Table as SATable

            metadata = MetaData()
            apscheduler_jobs = SATable(
                'apscheduler_jobs',
                metadata,
                Column('id', String(191), primary_key=True),
                Column('next_run_time', Float(25)),
                Column('job_state', LargeBinary, nullable=False),
                Index('ix_apscheduler_jobs_next_run_time', 'next_run_time')
            )
            metadata.create_all(engine)
            logger.info("[Scheduler] APScheduler tables created")
    except Exception as e:
        logger.warning("[Scheduler] Could not create APScheduler tables: %s", e)

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
        "record_check_interval_minutes": int(get_config("record_check_interval_minutes", 10)),
        "email_retry_interval_minutes": int(get_config("email_retry_interval_minutes", 1)),
        "cleanup_interval_days": int(get_config("cleanup_interval_days", 1)),
        "cleanup_retry_queue_interval_days": int(get_config("cleanup_retry_queue_interval_days", 1)),
        "wa_daily_report_hour": int(get_config("wa_daily_report_hour", 8)),
        "wa_daily_report_minute": int(get_config("wa_daily_report_minute", 0)),
        # Cron expressions
        "snapshot_cron": get_config("snapshot_cron", ""),
        "healthcheck_cron": get_config("healthcheck_cron", ""),
        "storage_check_cron": get_config("storage_check_cron", ""),
        "record_check_cron": get_config("record_check_cron", ""),
        "cleanup_cron": get_config("cleanup_cron", "0 2 * * *"),
        "email_retry_cron": get_config("email_retry_cron", ""),
    }

    scheduler.timezone = scheduler_timezone(get_scheduler_timezone())
    config["timezone"] = str(scheduler.timezone)
    job_schedules = {job_id: get_job_schedule(job_id) for job_id in JOB_SCHEDULES}
    config["job_schedules"] = job_schedules
    last_config.update(config)

    # Main jobs with cron support
    scheduler.add_job(
        scheduled_snapshot,
        trigger=build_job_trigger(job_schedules['scheduled_snapshot'], scheduler.timezone),
        id="scheduled_snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
        replace_existing=True,
    )

    scheduler.add_job(
        health_check_job,
        trigger=build_job_trigger(job_schedules['health_check'], scheduler.timezone),
        id="health_check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
    )

    # Cleanup jobs with cron support (default: 2 AM daily)
    cleanup_jobs = [
        ("cleanup_camera_stats", delete_old_camera_stats),
        ("cleanup_api_logs", delete_old_api_logs),
        ("cleanup_command_logs", delete_old_command_logs),
        ("cleanup_record_checks", cleanup_record_checks_job),
    ]

    cleanup_job_wrappers = {
        'cleanup_camera_stats': cleanup_camera_stats_job,
        'cleanup_api_logs': cleanup_api_logs_job,
        'cleanup_command_logs': cleanup_command_logs_job,
        'cleanup_record_checks': cleanup_record_checks_job,
    }
    for job_id, func in cleanup_jobs:
        scheduler.add_job(
            cleanup_job_wrappers[job_id],
            trigger=build_job_trigger(job_schedules[job_id], scheduler.timezone),
            id=job_id,
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )

    # Email retry with cron support
    scheduler.add_job(
        email_retry_job,
        trigger=build_job_trigger(job_schedules['email_retry'], scheduler.timezone),
        id='email_retry',
        max_instances=1,
        coalesce=True,
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_email_retry_job,
        trigger=build_job_trigger(job_schedules['cleanup_email_retry'], scheduler.timezone),
        id="cleanup_email_retry",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # Storage monitoring with cron support
    scheduler.add_job(
        storage_check_job,
        trigger=build_job_trigger(job_schedules['storage_check'], scheduler.timezone),
        id='storage_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )

    scheduler.add_job(
        record_folder_check_job,
        trigger=build_job_trigger(job_schedules['record_folder_check'], scheduler.timezone),
        id='record_folder_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )

    # WhatsApp daily report with configurable time
    scheduler.add_job(
        wa_daily_report_job,
        trigger=build_job_trigger(job_schedules['wa_daily_report'], scheduler.timezone),
        id="wa_daily_report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )

    scheduler.add_job(
        record_check_daily_report_job,
        trigger=build_job_trigger(job_schedules['record_check_daily_report'], scheduler.timezone),
        id="record_check_daily_report",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True,
    )

    # Orphaned snapshots check - runs every hour
    scheduler.add_job(
        orphaned_snapshots_check_job,
        trigger=build_job_trigger(job_schedules['orphaned_snapshots_check'], scheduler.timezone),
        id='orphaned_snapshots_check',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        replace_existing=True
    )

    # MED-003: Retention policy enforcement - runs daily at 3 AM
    scheduler.add_job(
        retention_policy_job,
        trigger=build_job_trigger(job_schedules['retention_policy'], scheduler.timezone),
        id='retention_policy',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True
    )

    scheduler.start()
    logger.info("[Scheduler] Started with %d jobs", len(scheduler.get_jobs()))
    return scheduler


def update_scheduler_config() -> bool:
    """Reload each job's schedule independently from database configuration."""
    try:
        update_scheduler_timezone()
        previous_schedules = last_config.get("job_schedules", {})
        schedules = {}
        for job_id in JOB_SCHEDULES:
            settings = get_job_schedule(job_id)
            schedules[job_id] = settings
            if settings != previous_schedules.get(job_id):
                job = scheduler.get_job(job_id)
                if job:
                    trigger = build_job_trigger(settings, scheduler.timezone)
                    job.reschedule(trigger=trigger)
                    logger.info("[Scheduler] Rescheduled %s with trigger: %s", job_id, trigger)
        last_config["job_schedules"] = schedules
        for key, default in (
            ("snapshot_concurrent_workers", 5),
            ("snapshot_batch_size", 50),
            ("snapshot_batch_delay_seconds", 5),
        ):
            value = int(get_config(key, default))
            if value != last_config.get(key):
                CONFIG_HANDLERS[key](scheduler, value)
                last_config[key] = value
        return True
    except Exception:
        logger.exception("[Scheduler] Failed to reload config")
        return False


# ----------------------------
# Job Wrappers with logged_job decorator
# These wrapper functions are needed for proper APScheduler serialization
# when using SQLAlchemyJobStore. Inline decorator application doesn't work
# because the wrapper function can't be properly serialized/deserialized.
# ----------------------------

@logged_job("health_check", "Health Check")
def health_check_job():
    """Wrapper for health check job with logging."""
    return ping_all_devices()


@logged_job("storage_check", "Storage Check")
def storage_check_job():
    """Wrapper for storage check job with logging."""
    return check_storage_job()


@logged_job("record_folder_check", "Record Folder Check")
def record_folder_check_job():
    """Wrapper for mounted record folder checks with logging."""
    return check_record_folders_job()


@logged_job("email_retry", "Email Retry")
def email_retry_job():
    """Wrapper for email retry job with logging."""
    return process_email_retry_queue()


@logged_job("cleanup_email_retry", "Email Retry Cleanup")
def cleanup_email_retry_job():
    """Wrapper for cleanup email retry job with logging."""
    return cleanup_email_retry_queue()


@logged_job("cleanup_camera_stats", "Cleanup Camera Stats")
def cleanup_camera_stats_job():
    """Wrapper for cleanup camera stats job with logging."""
    return delete_old_camera_stats()


@logged_job("cleanup_api_logs", "Cleanup API Logs")
def cleanup_api_logs_job():
    """Wrapper for cleanup API logs job with logging."""
    return delete_old_api_logs()


@logged_job("cleanup_command_logs", "Cleanup Command Logs")
def cleanup_command_logs_job():
    """Wrapper for cleanup command logs job with logging."""
    return delete_old_command_logs()


@logged_job("cleanup_record_checks", "Cleanup Record Checks")
def cleanup_record_checks_job():
    """Wrapper for record-check history cleanup job with logging."""
    return delete_old_record_checks()


@logged_job("record_check_daily_report", "Record Check Daily Report")
def record_check_daily_report_job():
    """Wrapper for daily record-check WhatsApp reports with trend PDF."""
    return send_record_check_daily_report()


@logged_job("wa_daily_report", "WhatsApp Daily Report")
def wa_daily_report_job():
    """Wrapper for WhatsApp daily report job with logging."""
    return send_wa_camera_no_snapshot_report()


@logged_job("orphaned_snapshots_check", "Orphaned Snapshots Check")
def orphaned_snapshots_check_job():
    """Wrapper for orphaned snapshots check job with logging."""
    return check_orphaned_snapshots_job()


@logged_job("retention_policy", "Retention Policy Enforcement")
def retention_policy_job():
    """MED-003: Wrapper for retention policy enforcement job with logging."""
    return enforce_retention_policy()


# ----------------------------
# Cleanup Jobs
# ----------------------------
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


def delete_old_camera_stats():
    db: Session = SessionLocal()
    try:
        retention_days = int(get_config("retention_camera_stats_days", 90))
        cutoff_date = to_current_timezone(datetime.now(timezone.utc), db).date() - timedelta(
            days=retention_days
        )
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


# MED-003: Retention Policy Enforcement for Snapshots and Videos
def enforce_retention_policy():
    """MED-003: Automated retention policy enforcement for snapshots and videos.
    
    Soft-deletes snapshots and videos older than retention period.
    Items with retention_hold=True are skipped.
    """
    db: Session = SessionLocal()
    try:
        from app.models.snapshot import Snapshot
        from app.models.video import Video
        
        # Get retention settings from config
        snapshot_retention_days = int(get_config("retention_snapshot_days", 30))
        video_retention_days = int(get_config("retention_video_days", 7))
        
        now = datetime.now(timezone.utc)
        results = {
            "snapshots_soft_deleted": 0,
            "videos_soft_deleted": 0,
            "snapshots_skipped_retention_hold": 0,
            "videos_skipped_retention_hold": 0
        }
        
        # Process snapshots
        snapshot_cutoff = now - timedelta(days=snapshot_retention_days)
        old_snapshots = db.query(Snapshot).filter(
            Snapshot.timestamp < snapshot_cutoff,
            Snapshot.deleted_at.is_(None)  # Not already soft-deleted
        ).all()
        
        for snapshot in old_snapshots:
            # MED-003: Skip items with retention hold
            if getattr(snapshot, 'retention_hold', False):
                results["snapshots_skipped_retention_hold"] += 1
                logger.info("[Retention Policy] Snapshot %s skipped due to retention hold", snapshot.id)
                continue
            
            # Soft delete
            snapshot.soft_delete()
            results["snapshots_soft_deleted"] += 1
        
        # Process videos
        video_cutoff = now - timedelta(days=video_retention_days)
        old_videos = db.query(Video).filter(
            Video.timestamp < video_cutoff,
            Video.deleted_at.is_(None)  # Not already soft-deleted
        ).all()
        
        for video in old_videos:
            # MED-003: Skip items with retention hold
            if getattr(video, 'retention_hold', False):
                results["videos_skipped_retention_hold"] += 1
                logger.info("[Retention Policy] Video %s skipped due to retention hold", video.id)
                continue
            
            # Soft delete
            video.soft_delete()
            results["videos_soft_deleted"] += 1
        
        db.commit()
        
        total_processed = results["snapshots_soft_deleted"] + results["videos_soft_deleted"]
        logger.info(
            "[Retention Policy] Enforced: %d snapshots, %d videos soft-deleted. "
            "Skipped: %d snapshots, %d videos (retention hold)",
            results["snapshots_soft_deleted"],
            results["videos_soft_deleted"],
            results["snapshots_skipped_retention_hold"],
            results["videos_skipped_retention_hold"]
        )
        
        return {"records_processed": total_processed, "details": results}
        
    except Exception as e:
        db.rollback()
        logger.error("[Retention Policy] Error: %s", e, exc_info=True)
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


def check_record_folders_job():
    """Monitor mounted SMB/NVR recording folders."""
    db: Session = SessionLocal()
    try:
        result = run_all_record_checks(db, send_notifications=True)
        logger.info(
            "[Record Check] Checked %d sources, failed %d, processed %d folders",
            result.get("sources_checked", 0),
            result.get("sources_failed", 0),
            result.get("records_processed", 0),
        )
        return result
    except Exception as e:
        logger.error("[Record Check] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def delete_old_record_checks():
    """Delete old record-check run, folder-check, and event history."""
    db: Session = SessionLocal()
    try:
        retention_days = int(get_config("retention_record_check_days", 90))
        if retention_days <= 0:
            logger.info("[Record Check Cleanup] Disabled because retention_record_check_days=%s", retention_days)
            return {"records_processed": 0, "retention_days": retention_days}

        deleted = cleanup_old_record_checks(db, retention_days)
        logger.info(
            "[Record Check Cleanup] Deleted %d rows older than %d days",
            deleted,
            retention_days,
        )
        return {"records_processed": deleted, "retention_days": retention_days}
    except Exception as e:
        logger.error("[Record Check Cleanup] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()


def send_record_check_daily_report():
    """Send daily WhatsApp record-check report and optional trend PDF."""
    db: Session = SessionLocal()
    try:
        result = send_record_check_daily_reports(db)
        logger.info(
            "[Record Check Daily Report] Sent %d messages for %d sources",
            result.get("records_processed", 0),
            result.get("sources", 0),
        )
        return result
    except Exception as e:
        logger.error("[Record Check Daily Report] Error: %s", e, exc_info=True)
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
                # BUG FIX: Use task.created_at as incident_time for retry
                # This preserves the original detection time from first attempt
                incident_time = task.created_at
                if incident_time.tzinfo is None:
                    incident_time = incident_time.replace(tzinfo=timezone.utc)
                ok = bool(send_tamper_alert(db, cam, task.reason, task.file_path, incident_time=incident_time))
            elif task.type == "recovery":
                ok = bool(send_recovery_alert(db, cam))
            elif task.type == "offline":
                ok = bool(send_offline_incident_email_once(
                    db, camera=cam,
                    incident_started_at=now - timedelta(minutes=30),
                    offline_duration_seconds=1800,
                ))
            elif task.type == "online":
                # NOTE: Online notifications (offline -> online) are intentionally disabled
                # as per requirement. Only offline alerts and tamper/recovery alerts are sent.
                logger.info("[ONLINE] Camera %s is back online - no email sent (as per policy)", cam.hostname)
                ok = True  # Mark as processed but don't send email
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
        
        now = datetime.now(timezone.utc)
        local_now = to_current_timezone(now, db)
        snapshot_cutoff = now - timedelta(days=7)
        cameras = db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"])).all()
        
        no_snapshot_cameras = []
        for cam in cameras:
            recent_snapshot = (
                db.query(SnapshotLog)
                .filter(SnapshotLog.camera_id == cam.id)
                .filter(SnapshotLog.timestamp >= snapshot_cutoff)
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
        lines.append(f"Date: {local_now:%Y-%m-%d %H:%M %Z}\n")
        
        if no_snapshot_cameras:
            lines.append(f"⚠️ *Cameras without snapshots in the last 7 days:* {len(no_snapshot_cameras)}")
            for cam in no_snapshot_cameras[:50]:
                camera_label = cam.hostname or cam.ip or str(cam.id)
                if cam.ip and cam.hostname:
                    camera_label = f"{cam.hostname} ({cam.ip})"
                lines.append(f"• {camera_label}")
            lines.append("")
        
        message = "\n".join(lines)
        
        receivers = [r.strip() for r in receiver.split(",") if r.strip()]
        sent_count = 0
        for phone in receivers:
            phone = phone if "@" in phone else format_phone_number(phone)
            result = wa_service.send_text(phone, message)
            if result["success"]:
                sent_count += 1
        
        return {"records_processed": sent_count}
    except Exception as e:
        logger.error("[WA Report] Error: %s", e, exc_info=True)
        raise
    finally:
        db.close()
