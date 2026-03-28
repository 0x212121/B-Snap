from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.timezone_helper import get_current_timezone, to_current_timezone, format_datetime_standard
import io
import csv
from app.utils.template_helper import templates

router = APIRouter()


def format_datetime_local(dt, tz_name=None, db=None):
    """
    Convert datetime object or string timestamp to configured timezone
    Uses standard format: DD/MM/YYYY - HH:MM:SS TZ
    """
    if not dt:
        return ""

    dt_obj = None
    if isinstance(dt, str):
        try:
            dt_obj = datetime.fromisoformat(dt)
        except ValueError:
            try:
                dt_obj = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return dt
    elif isinstance(dt, datetime):
        dt_obj = dt
    else:
        return str(dt)

    # Use the standard format function
    return format_datetime_standard(dt_obj, db=db)


# Daftarkan filter agar bisa digunakan di template
templates.env.filters["format_datetime"] = format_datetime_local
# --------------------------------------


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Render audit logs page with enhanced UI."""
    tz_name = get_current_timezone(db)

    return templates.TemplateResponse("audit_logs.html", {
        "request": request,
        "timezone": tz_name,
    })


@router.get("/api/audit-logs", response_class=JSONResponse)
async def get_audit_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required)
):
    """
    Endpoint API untuk mendapatkan data audit log dengan stats.
    Mendukung filtering dan paginasi.
    """
    query = db.query(AuditLog)

    # Terapkan filter berdasarkan parameter
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

    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    
    if end_date:
        end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        query = query.filter(AuditLog.timestamp < end_date_dt)

    # Hitung total setelah filter diterapkan
    total_logs = query.count()
    total_pages = (total_logs + per_page - 1) // per_page if total_logs > 0 else 1

    # Ambil data untuk halaman saat ini
    logs = query.order_by(AuditLog.timestamp.desc())\
               .offset((page - 1) * per_page)\
               .limit(per_page)\
               .all()

    # Calculate stats
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.query(AuditLog).filter(AuditLog.timestamp >= today_start).count()
    
    unique_users = db.query(AuditLog.user).distinct().count()
    
    # Get top action
    top_action_result = db.query(
        AuditLog.action, 
        func.count(AuditLog.id).label("count")
    ).group_by(AuditLog.action).order_by(func.count(AuditLog.id).desc()).first()
    top_action = top_action_result[0] if top_action_result else None

    logs_data = []
    for log in logs:
        ts_local = format_datetime_standard(log.timestamp, db=db)

        logs_data.append({
            "timestamp": ts_local,
            "user": log.user,
            "action": log.action,
            "target": log.target,
            "ip": log.ip,
            "extra": log.extra,
            "request_method": getattr(log, 'request_method', None),
            "response_status": getattr(log, 'response_status', None),
            "user_agent": getattr(log, 'user_agent', None),
            "request_path": getattr(log, 'request_path', None),
        })

    return {
        "logs": logs_data,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_logs": total_logs,
        "total": total_logs,
        "timezone": get_current_timezone(db),
        "stats": {
            "today": today_count,
            "unique_users": unique_users,
            "top_action": top_action,
        }
    }


# Endpoint for CSV export
@router.get("/api/audit-logs/export")
async def export_audit_logs_csv(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required)
):
    # Logika query sama persis dengan di atas, tapi tanpa paginasi
    query = db.query(AuditLog)
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(or_(AuditLog.user.ilike(search_term), AuditLog.target.ilike(search_term)))
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        query = query.filter(AuditLog.timestamp < end_date_dt)

    logs = query.order_by(AuditLog.timestamp.desc()).all()
    
    # Buat file CSV di memori
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User", "Action", "Target", "IP Address", "Extra"])
    for log in logs:
        writer.writerow([
            format_datetime_standard(log.timestamp, db=db),
            log.user, log.action, log.target, log.ip, log.extra
        ])
    
    csv_content = output.getvalue()
    output.close()
    
    return StreamingResponse(
        io.BytesIO(csv_content.encode('utf-8')),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=audit_logs_export.csv"}
    )
