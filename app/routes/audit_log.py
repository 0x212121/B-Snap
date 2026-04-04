"""Audit log routes for B-Snap."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.audit_log import AuditLog, AuditLogLegacy, AuditArchiveHistory
from app.routes.auth import admin_access_required
from app.models.user import User
from app.utils.template_helper import templates
from app.utils.timezone_helper import (
    format_datetime_standard, 
    get_current_timezone,
    utc_now
)
from app.jobs.audit_archive import AuditArchiveService, get_archive_stats

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("/")
async def audit_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
):
    """Render audit logs page dengan timezone format Asia/Makassar."""
    tz_name = get_current_timezone(db)  # "Asia/Makassar"
    
    return templates.TemplateResponse(
        "audit_logs.html",
        {
            "request": request,
            "timezone": tz_name,  # Format: Asia/Makassar, Asia/Jakarta
        },
    )


@router.get("/api")
async def get_audit_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
    action: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    include_legacy: bool = False,
):
    """Get audit logs dengan filtering dan timezone yang benar."""
    offset = (page - 1) * per_page
    
    # Query dasar
    query = db.query(AuditLog)
    
    # Apply filters
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (AuditLog.user.ilike(search_filter)) |
            (AuditLog.target.ilike(search_filter)) |
            (AuditLog.ip.ilike(search_filter)) |
            (AuditLog.request_path.ilike(search_filter))
        )
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    
    if method:
        query = query.filter(AuditLog.request_method == method.upper())
    
    if status_code:
        query = query.filter(AuditLog.response_status == status_code)
    
    # Date filter dengan timezone handling yang benar
    tz_name = get_current_timezone(db)
    
    if start_date:
        try:
            # Parse sebagai local timezone lalu convert ke UTC untuk query DB
            start_local = datetime.strptime(start_date, "%Y-%m-%d")
            start_utc = to_utc_for_query(start_local, tz_name)
            query = query.filter(AuditLog.timestamp >= start_utc)
        except ValueError:
            pass
    
    if end_date:
        try:
            end_local = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            end_utc = to_utc_for_query(end_local, tz_name)
            query = query.filter(AuditLog.timestamp < end_utc)
        except ValueError:
            pass
    
    # Pagination
    total = query.count()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    logs = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(per_page).all()
    
    # Stats dengan timezone yang benar
    now_utc = utc_now()
    # Convert UTC midnight ke local untuk hitung "today"
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    
    today_count = db.query(AuditLog).filter(AuditLog.timestamp >= today_start).count()
    unique_users = db.query(AuditLog.user).distinct().count()
    
    # Security events
    security_actions = [
        'view_camera_password', 'view_nvr_password', 
        'retention_hold_enabled', 'retention_hold_disabled',
        'purge_snapshot', 'delete_camera', 'delete_user'
    ]
    security_count = db.query(AuditLog).filter(
        AuditLog.action.in_(security_actions)
    ).count()
    
    # Archive stats (6 bulan lalu dari sekarang dalam local timezone)
    cutoff_date = now_utc - timedelta(days=180)
    eligible_for_archive = db.query(AuditLog).filter(
        AuditLog.timestamp < cutoff_date
    ).count()
    
    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                # Format: "30/03/2026 - 16:57:00 WITA"
                "timestamp": format_datetime_standard(log.timestamp, db=db) if log.timestamp else None,
                "user": log.user,
                "action": log.action,
                "target": log.target,
                "ip": log.ip,
                "extra": log.extra,
                "user_agent": log.user_agent,
                "request_path": log.request_path,
                "request_method": log.request_method,
                "response_status": log.response_status,
            }
            for log in logs
        ],
        "total": total,
        "total_logs": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "timezone": tz_name,  # "Asia/Makassar" untuk konsistensi dengan header
        "stats": {
            "today": today_count,
            "unique_users": unique_users,
            "security_events": security_count,
            "eligible_for_archive": eligible_for_archive,
        }
    })


def to_utc_for_query(naive_dt: datetime, tz_name: str) -> datetime:
    """Helper: Convert naive local datetime ke UTC untuk query DB."""
    import pytz
    if naive_dt.tzinfo is None:
        local_tz = pytz.timezone(tz_name)
        aware_dt = local_tz.localize(naive_dt)
    else:
        aware_dt = naive_dt
    return aware_dt.astimezone(pytz.UTC)


@router.get("/api/stats")
async def get_audit_logs_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
):
    """Get comprehensive audit log statistics including archive info."""
    stats = get_archive_stats(db)
    return JSONResponse(stats)


@router.post("/api/archive")
async def trigger_archive(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
):
    """Trigger manual archive of logs older than 6 months.
    
    This endpoint:
    1. Exports logs > 6 months to encrypted file
    2. Copies to legacy table
    3. Verifies integrity
    4. Removes from current table
    """
    try:
        service = AuditArchiveService(db)
        result = service.archive_logs(archived_by=current_user.username or "admin")
        
        return JSONResponse({
            "status": "success",
            "message": result.get("message", "Archive completed"),
            "data": result
        })
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Archive failed: {str(e)}"
            }
        )


@router.get("/api/archive/history")
async def get_archive_history(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    page: int = 1,
    per_page: int = 10,
):
    """Get archive operation history."""
    offset = (page - 1) * per_page
    
    query = db.query(AuditArchiveHistory).order_by(
        AuditArchiveHistory.archived_at.desc()
    )
    
    total = query.count()
    history = query.offset(offset).limit(per_page).all()
    
    return JSONResponse({
        "history": [
            {
                "id": h.id,
                "archived_at": h.archived_at.isoformat() if h.archived_at else None,
                "archived_by": h.archived_by,
                "records_archived": h.records_archived,
                "file_path": h.file_path,
                "file_size_mb": round(h.file_size_bytes / (1024 * 1024), 2) if h.file_size_bytes else 0,
                "checksum": h.checksum,
                "status": h.status,
                "notes": h.notes,
            }
            for h in history
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    })


@router.get("/api/legacy")
async def get_legacy_logs(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
):
    """Query legacy audit logs (archived data > 6 months)."""
    offset = (page - 1) * per_page
    
    query = db.query(AuditLogLegacy)
    
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (AuditLogLegacy.user.ilike(search_filter)) |
            (AuditLogLegacy.target.ilike(search_filter))
        )
    
    total = query.count()
    logs = query.order_by(AuditLogLegacy.timestamp.desc()).offset(offset).limit(per_page).all()
    
    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "timestamp": format_datetime_standard(log.timestamp, db=db) if log.timestamp else None,
                "user": log.user,
                "action": log.action,
                "target": log.target,
                "ip": log.ip,
                "extra": log.extra,
                "user_agent": log.user_agent,
                "request_path": log.request_path,
                "request_method": log.request_method,
                "response_status": log.response_status,
                "source": "legacy"
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    })
