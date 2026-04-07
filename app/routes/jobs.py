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

router = APIRouter(prefix="/admin/jobs", tags=["Job Management"])

# Direct database access for reading jobs (no scheduler needed)
from sqlalchemy import text

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
        # Get cron expressions from config
        cleanup_cron = get_config('cleanup_cron', '0 2 * * *')
        cron_configs = {
            'scheduled_snapshot': get_config('snapshot_cron', ''),
            'health_check': get_config('healthcheck_cron', ''),
            'storage_check': get_config('storage_check_cron', ''),
            'cleanup_audit_logs': cleanup_cron,
            'cleanup_camera_stats': cleanup_cron,
            'cleanup_api_logs': cleanup_cron,
            'cleanup_command_logs': cleanup_cron,
            'email_retry': get_config('email_retry_cron', ''),
        }
        
        # Read jobs directly from database
        db_jobs = get_jobs_from_db()
        
        jobs = []
        for db_job in db_jobs:
            job_id = db_job['id']
            
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
                "name": job_id.replace('_', ' ').title(),
                "trigger": "Cron/Interval",  # Simplified, read from config
                "cron_expression": cron_expr,
                "next_run_time": next_run_iso,
                "last_run": {
                    "started_at": last_run.started_at.isoformat() if last_run else None,
                    "status": last_run.status if last_run else None,
                    "duration_ms": last_run.duration_ms if last_run else None,
                } if last_run else None,
                "stats_24h": job_stats,
            })
        
        return JSONResponse({
            "jobs": jobs,
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
    
    # Note: To actually trigger a job immediately, we need to:
    # 1. Use APScheduler's modify_job (requires scheduler instance)
    # 2. Or implement a message queue between web app and scheduler
    # For now, this is a placeholder that requires manual implementation
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
        "email_retry_interval_minutes": int(get_config("email_retry_interval_minutes", 1)),
        "cleanup_interval_days": int(get_config("cleanup_interval_days", 1)),
        "wa_daily_report_hour": int(get_config("wa_daily_report_hour", 8)),
        "wa_daily_report_minute": int(get_config("wa_daily_report_minute", 0)),
        "wa_storage_alert_interval_hours": int(get_config("wa_storage_alert_interval_hours", 2)),
        "retention_job_logs_days": int(get_config("retention_job_logs_days", 30)),
    })


@router.post("/api/jobs/{job_id}/schedule")
async def update_job_schedule(
    job_id: str,
    cron_expression: str = Query(..., description="Cron expression (e.g., '0 8,13,23 * * *')"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Update cron schedule for a job.
    
    Job IDs mapping to config keys:
    - scheduled_snapshot -> snapshot_cron
    - health_check -> healthcheck_cron
    - storage_check -> storage_check_cron
    - cleanup_* -> cleanup_cron
    - email_retry -> email_retry_cron
    """
    # Map job_id to config key
    job_to_config = {
        'scheduled_snapshot': 'snapshot_cron',
        'health_check': 'healthcheck_cron',
        'storage_check': 'storage_check_cron',
        'cleanup_audit_logs': 'cleanup_cron',
        'cleanup_camera_stats': 'cleanup_cron',
        'cleanup_api_logs': 'cleanup_cron',
        'cleanup_command_logs': 'cleanup_cron',
        'email_retry': 'email_retry_cron',
    }
    
    config_key = job_to_config.get(job_id)
    if not config_key:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Cannot update schedule for job '{job_id}'"}
        )
    
    # Validate cron expression (basic check)
    cron_parts = cron_expression.strip().split()
    if len(cron_parts) != 5:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid cron expression. Must have 5 parts: minute hour day month weekday"}
        )
    
    try:
        # Update config in database
        config = db.query(Configuration).filter(Configuration.key == config_key).first()
        if config:
            config.value = cron_expression
        else:
            config = Configuration(key=config_key, value=cron_expression)
            db.add(config)
        db.commit()
        
        return JSONResponse({
            "status": "success",
            "message": f"Schedule updated for '{job_id}'. Changes will take effect on next config reload.",
            "cron_expression": cron_expression
        })
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

# Append to the end of existing file content
