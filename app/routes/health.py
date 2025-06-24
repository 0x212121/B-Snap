from collections import Counter
from datetime import datetime, timedelta, date, timezone
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import asc, union_all, literal_column, select
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.routes.auth import operator_access_required
from app.utils.health_check import run_healthcheck_for_all, run_healthcheck_for_camera, run_healthcheck_for_nvr
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from app.models_sql import NVR, CameraDailyStats, CameraHealth as Health, Camera as DBCamera, HealthCheckStatus, User
import pytz
from pydantic import BaseModel, Field, ConfigDict # Import ConfigDict

router = APIRouter()
templates = Jinja2Templates(directory="templates")
wita_tz = pytz.timezone('Asia/Makassar')

class DeviceHealthStatus(BaseModel):
    id: str
    hostname: str
    ip: str
    dev_status: str
    type: str
    status: str | None
    latency: float | None
    checked_at: datetime | None = Field(None, alias="checked")
    last_online_at: datetime | None = Field(None, alias="last_online")
    status_changed_at: datetime | None

    # REFAKTORISASI: Gunakan ConfigDict dan 'from_attributes' untuk Pydantic v2+
    model_config = ConfigDict(
        from_attributes=True,  # Ini adalah pengganti 'orm_mode'
        populate_by_name=True, # Mengizinkan alias seperti 'checked' digunakan
    )

class HealthStatusResponse(BaseModel):
    statuses: list[DeviceHealthStatus]
    camera_online_count: int
    nvr_online_count: int
    camera_offline_count: int
    nvr_offline_count: int

# --- REFAKTORISASI 2: Fungsi Helper Terpusat untuk Query ---
# Prinsip DRY (Don't Repeat Yourself). Satu fungsi untuk mengambil data kesehatan
# dari NVR dan Kamera, digunakan oleh kedua endpoint.
def _get_all_devices_health_data(db: Session) -> list:
    """
    Mengambil dan menggabungkan data kesehatan dari Camera dan NVR menggunakan satu query UNION.
    Ini adalah cara paling efisien untuk mendapatkan data gabungan dari dua tabel serupa.
    """
    # Query untuk Camera
    camera_q = select(
        DBCamera.id, DBCamera.hostname, DBCamera.ip,
        DBCamera.status.label("dev_status"),
        literal_column("'Camera'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, DBCamera.id == Health.camera_id)

    # Query untuk NVR
    nvr_q = select(
        NVR.id, NVR.hostname, NVR.ip,
        NVR.status.label("dev_status"),
        literal_column("'NVR'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, NVR.id == Health.nvr_id)

    # Gabungkan dengan UNION ALL untuk performa terbaik
    unified_construct = union_all(camera_q, nvr_q).alias("unified_health")
    
    # Query final untuk select dan order
    final_query = select(unified_construct).order_by(asc(unified_construct.c.hostname))
    
    return db.execute(final_query).all()

# --- Endpoint Utama (Render Halaman Awal) ---
@router.get("/health", response_class=HTMLResponse)
async def health_monitor_page(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """
    Endpoint ini hanya merender halaman HTML dasar.
    Data akan dimuat secara dinamis oleh JavaScript saat halaman terbuka.
    Ini mempercepat waktu pemuatan awal halaman (First Contentful Paint).
    """
    return templates.TemplateResponse("health.html", {
        "request": request,
        # Data awal bisa dikosongkan, JS akan memanggil /health/status
        "initial_data": HealthStatusResponse(
            statuses=[], camera_online_count=0, nvr_online_count=0,
            camera_offline_count=0, nvr_offline_count=0
        ).json()
    })

# --- REFAKTORISASI 3: Endpoint API yang Dioptimalkan ---
@router.get("/health/status", response_model=HealthStatusResponse)
async def get_health_status_api(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    """
    API endpoint yang cepat dan efisien, hanya mengembalikan data JSON mentah.
    Semua formatting dan kalkulasi durasi dipindahkan ke klien (JavaScript).
    """
    try:
        all_devices = _get_all_devices_health_data(db)

        # Kalkulasi count dilakukan di backend secara efisien
        counts = Counter((dev.type, dev.status) for dev in all_devices)
        camera_online = counts[('Camera', 'Online')] + counts[('Camera', 'High Latency')]
        nvr_online = counts[('NVR', 'Online')] + counts[('NVR', 'High Latency')]
        camera_offline = counts[('Camera', 'Offline')]
        nvr_offline = counts[('NVR', 'Offline')]

        return HealthStatusResponse(
            statuses=all_devices,
            camera_online_count=camera_online,
            nvr_online_count=nvr_online,
            camera_offline_count=camera_offline,
            nvr_offline_count=nvr_offline,
        )
    except Exception as e:
        print(f"Error in get_health_status_api: {e}")
        # Mengembalikan error yang sesuai
        return JSONResponse(status_code=500, content={"message": "An internal error occurred."})

# ... (Endpoint lainnya seperti trigger_healthcheck, check_health_status tidak perlu banyak perubahan) ...
# Cukup pastikan mereka bekerja dengan baik dan menutup sesi DB.

@router.post("/health/trigger/{entity_id}")
async def trigger_healthcheck(
    entity_id: str, background_tasks: BackgroundTasks,
    type: str = Query(..., enum=["camera", "nvr"]),
    current_operator: User = Depends(operator_access_required)
):
    if type == "camera":
        background_tasks.add_task(run_healthcheck_for_camera, entity_id)
    elif type == "nvr":
        background_tasks.add_task(run_healthcheck_for_nvr, entity_id)
    return {"status": f"Healthcheck triggered for {type.upper()} ID {entity_id}"}


@router.post("/health/trigger_all")
async def trigger_healthcheck_all(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    # Kode ini sudah cukup baik menggunakan background task
    status = db.query(HealthCheckStatus).get(1)
    if not status:
        status = HealthCheckStatus(id=1)
        db.add(status)

    total_devices = db.query(DBCamera).count() + db.query(NVR).count() # Asumsi trigger semua
    
    status.is_running = True
    status.start_time = datetime.now(timezone.utc)
    status.total_cameras = total_devices # Mungkin perlu diubah nama kolomnya
    status.completed_cameras = 0
    db.commit()

    background_tasks.add_task(run_healthcheck_for_all)
    return {"status": "Healthcheck triggered for all devices"}


@router.get("/health/status/check")
async def check_healthcheck_status(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    # Kode ini sudah OK
    status = db.query(HealthCheckStatus).get(1)
    if not status or not status.is_running or status.completed_cameras >= status.total_cameras:
        if status and status.is_running:
            status.is_running = False
            db.commit()
        return {"is_complete": True}

    return {
        "is_complete": False,
        "progress": {
            "total": status.total_cameras,
            "completed": status.completed_cameras
        }
    }


# --- REFAKTORISASI 4: Optimasi Query pada Endpoint History ---
@router.get("/health/history", response_class=HTMLResponse)
async def health_history(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    from sqlalchemy.orm import contains_eager
    
    thirty_days_ago = date.today() - timedelta(days=30)

    # Optimasi: Filter data history di level database, bukan di Python.
    # Ini akan secara signifikan mengurangi memori dan waktu proses jika history-nya besar.
    cameras_with_history = db.query(DBCamera).outerjoin(
        CameraDailyStats, 
        (DBCamera.id == CameraDailyStats.camera_id) & (CameraDailyStats.date >= thirty_days_ago)
    ).options(
        contains_eager(DBCamera.daily_stats)
    ).order_by(asc(DBCamera.hostname)).all()

    historical_data = []
    for cam in cameras_with_history:
        if cam.daily_stats:
            # Data sudah terfilter, tinggal diurutkan jika perlu
            sorted_stats = sorted(cam.daily_stats, key=lambda x: x.date, reverse=True)
            historical_data.append({
                "hostname": cam.hostname,
                "stats": [{
                    "date": stat.date.strftime("%Y-%m-%d"),
                    "uptime_seconds": stat.total_uptime_seconds,
                    "downtime_seconds": stat.total_downtime_seconds,
                    "uptime_percentage": stat.uptime_percentage
                } for stat in sorted_stats]
            })

    return templates.TemplateResponse("health_history.html", {
        "request": request,
        "historical_data": historical_data
    })