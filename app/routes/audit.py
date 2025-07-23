from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models_sql import AuditLog, User
from app.routes.auth import admin_access_required
from app.utils.timezone_helper import get_current_timezone, to_current_timezone
from sqlalchemy import or_
import io
import csv
from app.utils.template_helper import templates

router = APIRouter()


def format_datetime_local(dt, tz_name=None):
    """
    Convert datetime object or string timestamp to configured timezone
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

    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=UTC)

    try:
        tz = ZoneInfo(tz_name or "UTC")
        return dt_obj.astimezone(tz).strftime("%d %B %Y, %H:%M:%S %Z")
    except Exception:
        return dt_obj.strftime("%d %B %Y, %H:%M:%S UTC")

# Daftarkan filter agar bisa digunakan di template
templates.env.filters["format_datetime"] = format_datetime_local
# --------------------------------------


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_admin: User = Depends(admin_access_required)
):
    from pytz import timezone
    tz_name = get_current_timezone(db)

    total_logs = db.query(AuditLog).count()
    logs = db.query(AuditLog)\
        .order_by(AuditLog.timestamp.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()

    # Ubah timestamp ke waktu lokal
    for log in logs:
        log.timestamp = to_current_timezone(log.timestamp, db)

    total_pages = (total_logs + per_page - 1) // per_page

    return templates.TemplateResponse("audit_logs.html", {
        "request": request,
        "logs": logs,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_logs": total_logs,
        "timezone": tz_name
    })


@router.get("/api/audit-logs", response_class=JSONResponse)
async def get_audit_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100), # Sesuaikan per_page di sini
    search: str | None = Query(None),
    action: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required)
):
    """
    Endpoint API untuk mendapatkan data audit log dalam format JSON.
    Mendukung filtering dan paginasi.
    """
    query = db.query(AuditLog)

    # Terapkan filter berdasarkan parameter
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            or_(
                AuditLog.user.ilike(search_term),
                AuditLog.target.ilike(search_term)
            )
        )
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))

    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    
    if end_date:
        # Tambah 1 hari ke end_date untuk membuatnya inklusif
        from datetime import datetime, timedelta
        end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        query = query.filter(AuditLog.timestamp < end_date_dt)

    # Hitung total setelah filter diterapkan
    total_logs = query.count()
    total_pages = (total_logs + per_page - 1) // per_page

    # Ambil data untuk halaman saat ini
    logs = query.order_by(AuditLog.timestamp.desc())\
               .offset((page - 1) * per_page)\
               .limit(per_page)\
               .all()

    logs_data = []
    for log in logs:
        ts_local = to_current_timezone(log.timestamp, db)

        logs_data.append({
            "timestamp": ts_local.isoformat(),
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
        "total_logs": total_logs,
        "timezone": get_current_timezone(db)
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
        from datetime import datetime, timedelta
        end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        query = query.filter(AuditLog.timestamp < end_date_dt)

    logs = query.order_by(AuditLog.timestamp.desc()).all()
    
    # Buat file CSV di memori
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "User", "Action", "Target", "IP Address", "Extra"])
    for log in logs:
        writer.writerow([
            to_current_timezone(log.timestamp, db).strftime("%Y-%m-%d %H:%M:%S"),
            log.user, log.action, log.target, log.ip, log.extra
        ])
    
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs_export.csv"}
    )