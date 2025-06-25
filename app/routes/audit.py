from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models_sql import AuditLog, User
from app.routes.auth import admin_access_required
from sqlalchemy import or_
import io
import csv
from pytz import UTC, timezone

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def format_datetime_local(dt, tz_name="Asia/Singapore"):
    """
    Convert datetime object or string timestamp to local timezone
    """
    if not dt: # Menangani None atau string kosong
        return ""

    dt_obj = None
    # Langkah 1: Periksa jika input adalah string, konversi ke objek datetime
    if isinstance(dt, str):
        try:
            # fromisoformat() adalah cara modern & cepat untuk parse YYYY-MM-DD HH:MM:SS
            dt_obj = datetime.fromisoformat(dt)
        except ValueError:
            # Fallback jika formatnya sedikit berbeda (misal tanpa mikrodetik)
            try:
                dt_obj = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                # Jika parsing tetap gagal, kembalikan string aslinya agar tidak error
                return dt
    elif isinstance(dt, datetime):
        # Jika sudah merupakan objek datetime, gunakan langsung
        dt_obj = dt
    else:
        # Jika tipe data tidak dikenali, kembalikan representasi stringnya
        return str(dt)


    # Langkah 2: Lanjutkan dengan logika timezone yang sudah ada
    if dt_obj.tzinfo is None:
        # Anggap timestamp 'naive' dari DB sebagai UTC
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    
    local_tz = ZoneInfo(tz_name)
    return dt_obj.astimezone(local_tz).strftime("%d %B %Y, %H:%M:%S WITA")


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
    Singapore_tz = timezone("Asia/Singapore")

    total_logs = db.query(AuditLog).count()
    logs = db.query(AuditLog)\
        .order_by(AuditLog.timestamp.desc())\
        .offset((page - 1) * per_page)\
        .limit(per_page)\
        .all()

    # Ubah timestamp ke waktu lokal
    for log in logs:
        log.timestamp = log.timestamp.astimezone(Singapore_tz)

    total_pages = (total_logs + per_page - 1) // per_page

    return templates.TemplateResponse("audit_logs.html", {
        "request": request,
        "logs": logs,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_logs": total_logs,
        "timezone": "Asia/Singapore"
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

    # Konversi data log ke format yang aman untuk JSON
    Singapore_tz = timezone("Asia/Singapore")

    logs_data = []
    for log in logs:
        ts = log.timestamp
        if ts.tzinfo is None:  # ⛔ naive datetime
            ts = UTC.localize(ts)
        ts_local = ts.astimezone(Singapore_tz)

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
        "timezone": "Asia/Singapore"
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
        writer.writerow([log.timestamp, log.user, log.action, log.target, log.ip, log.extra])
    
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs_export.csv"}
    )