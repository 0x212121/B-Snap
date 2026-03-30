from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.timezone_helper import (
    get_current_timezone,      # Returns "Asia/Makassar"
    format_datetime_standard # Returns "30/03/2026 - 16:55:00 WITA"
)
import io
import csv
from app.utils.template_helper import templates

router = APIRouter()


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Render audit logs page."""
    # Kirim full timezone name (Asia/Makassar) untuk header
    tz_name = get_current_timezone(db)

    return templates.TemplateResponse("audit_logs.html", {
        "request": request,
        "timezone": tz_name,  # Format: Asia/Makassar, Asia/Jakarta, UTC
    })


@router.get("/api/audit-logs")
async def get_audit_logs_api(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required)
):
    """API untuk data audit log dengan format timezone yang benar."""
    query = db.query(AuditLog)

    # Filter logic
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                AuditLog.user.ilike(search_term),
                AuditLog.target.ilike(search_term),
                AuditLog.ip.ilike(search_term)
            )
        )
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))

    # Date filter (convert ke UTC untuk query DB)
    tz_name = get_current_timezone(db)
    if start_date:
        # Parse sebagai local timezone, convert ke UTC untuk query
        start_dt = datetime.fromisoformat(start_date)
        if start_dt.tzinfo is None:
            import pytz
            start_dt = pytz.timezone(tz_name).localize(start_dt)
        query = query.filter(AuditLog.timestamp >= start_dt.astimezone(pytz.UTC))
    
    if end_date:
        end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        if end_dt.tzinfo is None:
            import pytz
            end_dt = pytz.timezone(tz_name).localize(end_dt)
        query = query.filter(AuditLog.timestamp < end_dt.astimezone(pytz.UTC))

    # Pagination
    total_logs = query.count()
    total_pages = (total_logs + per_page - 1) // per_page if total_logs > 0 else 1

    logs = query.order_by(AuditLog.timestamp.desc())\
               .offset((page - 1) * per_page)\
               .limit(per_page)\
               .all()

    # Stats (today dalam local timezone)
    now_local = datetime.now(pytz.timezone(tz_name))
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.query(AuditLog).filter(AuditLog.timestamp >= today_start).count()
    
    unique_users = db.query(AuditLog.user).distinct().count()
    
    top_action_result = db.query(
        AuditLog.action, 
        func.count(AuditLog.id).label("count")
    ).group_by(AuditLog.action).order_by(func.count(AuditLog.id).desc()).first()

    # Format data dengan timezone local (WITA/WIB)
    logs_data = []
    for log in logs:
        logs_data.append({
            "timestamp": format_datetime_standard(log.timestamp, db=db),  # "30/03/2026 - 16:55:00 WITA"
            "user": log.user,
            "action": log.action,
            "target": log.target,
            "ip": log.ip,
            "extra": log.extra,
        })

    return {
        "logs": logs_data,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total": total_logs,
        "timezone": tz_name,  # "Asia/Makassar"
        "stats": {
            "today": today_count,
            "unique_users": unique_users,
            "top_action": top_action_result[0] if top_action_result else None,
        }
    }


@router.get("/api/audit-logs/export")
async def export_audit_logs_csv(
    db: Session = Depends(get_db),
    search: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required)
):
    """Export CSV dengan format timezone yang benar."""
    query = db.query(AuditLog)
    
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(or_(
            AuditLog.user.ilike(search_term), 
            AuditLog.target.ilike(search_term)
        ))
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    
    # ... (date filter sama seperti di atas)
    
    logs = query.order_by(AuditLog.timestamp.desc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User", "Action", "Target", "IP Address", "Extra"])
    
    for log in logs:
        writer.writerow([
            format_datetime_standard(log.timestamp, db=db),  # Format local timezone
            log.user, 
            log.action, 
            log.target, 
            log.ip, 
            log.extra
        ])
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8')),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"}
    )