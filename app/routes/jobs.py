"""Job Management Routes.

Dashboard and API endpoints for managing background jobs.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from app.db.database import get_db, engine
from app.models.user import User
from app.models.job_execution_log import JobExecutionLog
from app.models.config import Configuration
from app.routes.auth import admin_access_required
from app.utils.template_helper import templates
from app.core.config import get_config
from app.core.job_schedules import JOB_SCHEDULES, get_job_schedule
from apscheduler.triggers.cron import CronTrigger
from app.utils.record_check_report import send_record_check_daily_reports

router = APIRouter(prefix="/admin/jobs", tags=["Job Management"])

# Direct database access for reading jobs (no scheduler needed)
from sqlalchemy import text

CONFIGURED_JOBS = {
    'scheduled_snapshot': 'Scheduled Snapshot',
    'health_check': 'Health Check',
    'storage_check': 'Storage Check',
    'record_folder_check': 'Record Folder Check',
    'cleanup_camera_stats': 'Cleanup Camera Stats',
    'cleanup_api_logs': 'Cleanup Api Logs',
    'cleanup_command_logs': 'Cleanup Command Logs',
    'cleanup_record_checks': 'Cleanup Record Checks',
    'email_retry': 'Email Retry',
    'cleanup_email_retry': 'Cleanup Email Retry',
    'wa_daily_report': 'Whatsapp Daily Report',
    'record_check_daily_report': 'Record Check Daily Report',
    'orphaned_snapshots_check': 'Orphaned Snapshots Check',
    'retention_policy': 'Retention Policy',
}

JOB_DESCRIPTIONS = {
    'scheduled_snapshot': 'Takes scheduled snapshots for active cameras and stores snapshot metadata.',
    'health_check': 'Checks camera and NVR health status, including online/offline state.',
    'storage_check': 'Records storage usage metrics and evaluates warning or critical thresholds.',
    'record_folder_check': 'Scans mounted NVR/SMB record folders, updates current folder status, and sends stale/missing/recovery alerts.',
    'cleanup_camera_stats': 'Deletes old camera daily statistics based on camera stats retention.',
    'cleanup_api_logs': 'Deletes old API request logs based on API log retention.',
    'cleanup_command_logs': 'Deletes old command logs based on command log retention.',
    'cleanup_record_checks': 'Hard-deletes old record-check run, folder-check, and event history. Sources, mappings, and current statuses are kept.',
    'email_retry': 'Processes queued email notifications that previously failed and are ready to retry.',
    'cleanup_email_retry': 'Deletes old completed or exhausted email retry queue rows.',
    'wa_daily_report': 'Sends a WhatsApp daily summary for cameras without recent snapshots and unhealthy cameras.',
    'record_check_daily_report': 'Sends a WhatsApp daily NVR record-check downtime report and optional 14-day trend PDF.',
    'orphaned_snapshots_check': 'Checks snapshot files and database metadata for orphaned or missing snapshot records.',
    'retention_policy': 'Soft-deletes old snapshots and videos according to retention settings. Items on retention hold are skipped.',
}

def get_jobs_from_db():
    """Read jobs directly from apscheduler_jobs table."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text('SELECT id, next_run_time FROM apscheduler_jobs ORDER BY id'))
            jobs = []
            for row in result:
                jobs.append({
                    'id': row.id,
                    'next_run_time': row.next_run_time
                })
            return jobs
    except Exception as e:
        # Table doesn't exist yet (scheduler hasn't started)
        import logging
        logging.getLogger(__name__).debug("apscheduler_jobs table not found: %s", e)
        return []

def get_job_from_db(job_id: str):
    """Read a single job from apscheduler_jobs table."""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text('SELECT id, next_run_time FROM apscheduler_jobs WHERE id = :job_id'),
                {'job_id': job_id}
            )
            row = result.first()
            if row:
                return {'id': row.id, 'next_run_time': row.next_run_time}
            return None
    except Exception as e:
        # Table doesn't exist yet (scheduler hasn't started)
        import logging
        logging.getLogger(__name__).debug("apscheduler_jobs table not found: %s", e)
        return None


@router.get("/", response_class=HTMLResponse)
async def jobs_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Render job management dashboard."""
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "title": "Job Management",
    })


@router.get("/api/jobs")
async def list_jobs(
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get list of all configured jobs with their settings."""
    try:
        schedules = {job_id: get_job_schedule(job_id) for job_id in CONFIGURED_JOBS}
        cron_configs = {job_id: settings["cron_expression"] for job_id, settings in schedules.items()}

        # Read jobs directly from database
        db_jobs_by_id = {job['id']: job for job in get_jobs_from_db()}
        
        jobs = []
        for job_id, job_name in CONFIGURED_JOBS.items():
            db_job = db_jobs_by_id.get(job_id, {'id': job_id, 'next_run_time': None})
            
            # Get last execution
            last_run = db.query(JobExecutionLog).filter(
                JobExecutionLog.job_id == job_id
            ).order_by(JobExecutionLog.started_at.desc()).first()
            
            # Get recent stats
            stats = JobExecutionLog.get_job_stats(db, hours=24)
            job_stats = stats.get(job_id, {})
            
            # Get cron expression for this job
            cron_expr = cron_configs.get(job_id, '')
            
            # Format next_run_time
            next_run = db_job['next_run_time']
            if next_run:
                next_run_iso = datetime.fromtimestamp(next_run, tz=timezone.utc).isoformat()
            else:
                next_run_iso = None
            
            jobs.append({
                "id": job_id,
                "name": job_name,
                "description": JOB_DESCRIPTIONS.get(job_id, "Background scheduler job."),
                "trigger": "Cron" if cron_expr else (
                    f"Every {schedules[job_id]['interval']} {schedules[job_id]['interval_unit']}"
                ),
                "schedule_editable": job_id in JOB_SCHEDULES,
                "cron_expression": cron_expr,
                "next_run_time": next_run_iso,
                "registered": job_id in db_jobs_by_id,
                "last_run": {
                    "started_at": last_run.started_at.isoformat() if last_run else None,
                    "status": last_run.status if last_run else None,
                    "duration_ms": last_run.duration_ms if last_run else None,
                } if last_run else None,
                "stats_24h": job_stats,
            })
        
        return JSONResponse({
            "jobs": jobs,
            "registered_job_ids": list(db_jobs_by_id.keys()),
            "cron_configs": cron_configs,
        })
    except Exception as e:
        import traceback
        print(f"ERROR in list_jobs: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/jobs/{job_id}/history")
async def job_history(
    job_id: str,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get execution history for a specific job."""
    logs = JobExecutionLog.get_recent_executions(db, job_id=job_id, limit=limit)
    
    return JSONResponse({
        "job_id": job_id,
        "history": [
            {
                "id": log.id,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "duration_ms": log.duration_ms,
                "status": log.status,
                "error_message": log.error_message,
                "records_processed": log.records_processed,
            }
            for log in logs
        ]
    })


@router.post("/api/jobs/{job_id}/run")
async def run_job_now(
    job_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Manually trigger a job to run immediately."""
    # Check if job exists
    job = get_job_from_db(job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Job '{job_id}' not found"}
        )
    
    if job_id == "record_check_daily_report":
        log = JobExecutionLog.start_execution(db, job_id, CONFIGURED_JOBS.get(job_id, job_id))
        try:
            result = send_record_check_daily_reports(db)
            records_processed = int(result.get("records_processed", 0))
            log.complete(db, "success", records=records_processed, metadata=result)
            return JSONResponse({
                "status": "success",
                "message": f"Job '{job_id}' completed. Sent {records_processed} message(s).",
                "result": result,
            })
        except Exception as exc:
            db.rollback()
            log.complete(db, "fail", error=str(exc))
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(exc)},
            )

    return JSONResponse({
        "status": "info",
        "message": f"Job '{job_id}' found. Manual triggering requires scheduler coordination (not yet implemented)."
    })


@router.post("/api/jobs/{job_id}/pause")
async def pause_job(
    job_id: str,
    current_admin: User = Depends(admin_access_required)
):
    """Pause a job."""
    # Check if job exists
    job = get_job_from_db(job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Job '{job_id}' not found"}
        )
    
    # Note: Pausing requires access to the running scheduler instance
    return JSONResponse({
        "status": "info",
        "message": f"Job '{job_id}' found. Pause/resume requires scheduler coordination (not yet implemented)."
    })


@router.post("/api/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    current_admin: User = Depends(admin_access_required)
):
    """Resume a paused job."""
    # Check if job exists
    job = get_job_from_db(job_id)
    if not job:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": f"Job '{job_id}' not found"}
        )
    
    try:
        job.resume()
        return JSONResponse({
            "status": "success",
            "message": f"Job '{job_id}' resumed"
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@router.get("/api/jobs/stats")
async def jobs_stats(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get overall job execution statistics."""
    try:
        stats = JobExecutionLog.get_job_stats(db, hours=hours)
        
        # Calculate overall stats
        total_runs = sum(s["total_runs"] for s in stats.values())
        total_success = sum(s["success_count"] for s in stats.values())
        total_fail = sum(s["fail_count"] for s in stats.values())
        
        # Ensure all values are JSON serializable (convert Decimal to float/int)
        def convert_to_serializable(obj):
            from decimal import Decimal
            if isinstance(obj, Decimal):
                return float(obj)
            return obj
        
        # Convert stats to ensure JSON serializability
        serializable_stats = {}
        for job_id, job_stats in stats.items():
            serializable_stats[job_id] = {
                k: convert_to_serializable(v) for k, v in job_stats.items()
            }
        
        return JSONResponse({
            "period_hours": hours,
            "overall": {
                "total_runs": int(total_runs),
                "success_count": int(total_success),
                "fail_count": int(total_fail),
                "success_rate": round(float(total_success) / float(total_runs) * 100, 1) if total_runs > 0 else 0.0,
            },
            "per_job": serializable_stats,
        })
    except Exception as e:
        import traceback
        print(f"ERROR in jobs_stats: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/jobs/cleanup-logs")
async def cleanup_job_logs(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Clean up old job execution logs."""
    try:
        deleted = JobExecutionLog.cleanup_old_logs(db, days=days)
        return JSONResponse({
            "status": "success",
            "deleted_rows": deleted,
            "message": f"Deleted {deleted} old job logs"
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@router.get("/api/job-config")
async def get_job_config(
    current_admin: User = Depends(admin_access_required)
):
    """Get current job interval configuration."""
    return JSONResponse({
        "snapshot_interval_minutes": int(get_config("snapshot_interval_minutes", 480)),
        "healthcheck_interval_minutes": int(get_config("healthcheck_interval_minutes", 15)),
        "storage_check_interval_hours": int(get_config("storage_check_interval_hours", 1)),
        "record_check_interval_minutes": int(get_config("record_check_interval_minutes", 10)),
        "email_retry_interval_minutes": int(get_config("email_retry_interval_minutes", 1)),
        "cleanup_interval_days": int(get_config("cleanup_interval_days", 1)),
        "wa_daily_report_hour": int(get_config("wa_daily_report_hour", 8)),
        "wa_daily_report_minute": int(get_config("wa_daily_report_minute", 0)),
        "retention_job_logs_days": int(get_config("retention_job_logs_days", 30)),
    })


@router.post("/api/jobs/{job_id}/schedule")
async def update_job_schedule(
    job_id: str,
    cron_expression: str = Query(..., description="Cron expression (e.g., '0 8,13,23 * * *')"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Update an individual job's cron override; empty restores its default."""
    definition = JOB_SCHEDULES.get(job_id)
    config_key = definition[0] if definition else None
    if not config_key:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Cannot update schedule for job '{job_id}'"}
        )
    
    cron_value = cron_expression.strip()

    if cron_value:
        try:
            CronTrigger.from_crontab(cron_value, timezone=timezone.utc)
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"Invalid cron expression: {exc}"},
            )

    try:
        # Update config in database
        config = db.query(Configuration).filter(Configuration.key == config_key).first()
        if config:
            config.value = cron_value
        else:
            config = Configuration(key=config_key, value=cron_value)
            db.add(config)
        db.commit()
        
        return JSONResponse({
            "status": "success",
            "message": f"Schedule updated for '{job_id}'. The scheduler applies changes within 60 seconds.",
            "cron_expression": cron_value,
        })
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# Append to the end of existing file content
