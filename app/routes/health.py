from collections import Counter
from datetime import datetime, timedelta, date, timezone
import math
from typing import Optional
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc, func, union_all, literal_column, select
from sqlalchemy.orm import Session
from app.core.config import get_config
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

# --- REFACTORING 2: Centralized Helper Function for Query ---
# DRY Principle (Don't Repeat Yourself). One function to fetch health data
# from NVR and Camera, used by both endpoints.
def _get_all_devices_health_data(db: Session) -> list:
    """
    Fetch and combine health data from Camera and NVR using a single UNION query.
    This is the most efficient way to get combined data from two similar tables.
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

    # Combine both queries into a single unified query
    unified_construct = union_all(camera_q, nvr_q).alias("unified_health")
    
    # Final query for select and order
    final_query = select(unified_construct).order_by(asc(unified_construct.c.hostname))
    
    return db.execute(final_query).all()

# --- Main Endpoint (Render Initial Page) ---
@router.get("/health", response_class=HTMLResponse)
async def health_monitor_page(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """
    This endpoint only renders the base HTML page.
    Data will be loaded dynamically by JavaScript when the page opens.
    This speeds up the initial page load time (First Contentful Paint).
    """
    return templates.TemplateResponse("health.html", {
        "request": request,
        # Initial data is empty, JS will call /health/status
        "initial_data": HealthStatusResponse(
            statuses=[], camera_online_count=0, nvr_online_count=0,
            camera_offline_count=0, nvr_offline_count=0
        ).json()
    })

# --- REFACTORING 3: Optimized API Endpoint ---
@router.get("/health/status", response_model=HealthStatusResponse)
async def get_health_status_api(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    """
    Fast and efficient API endpoint, only returns raw JSON data.
    All formatting and duration calculations are moved to the client (JavaScript).
    """
    try:
        all_devices = _get_all_devices_health_data(db)

        # Count calculation is done efficiently in the backend
        counts = Counter((dev.type, dev.status) for dev in all_devices)
        camera_online = counts[("Camera", "Online")] + counts[("Camera", "High Latency")]
        nvr_online = counts[("NVR", "Online")] + counts[("NVR", "High Latency")]
        camera_offline = counts[("Camera", "Offline")]
        nvr_offline = counts[("NVR", "Offline")]

        return HealthStatusResponse(
            statuses=all_devices,
            camera_online_count=camera_online,
            nvr_online_count=nvr_online,
            camera_offline_count=camera_offline,
            nvr_offline_count=nvr_offline,
        )
    except Exception as e:
        print("Error in get_health_status_api: %s" % e)
        # Return appropriate error
        return JSONResponse(status_code=500, content={"message": "An internal error occurred."})


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
    # This code is already good using background task
    status = db.query(HealthCheckStatus).get(1)
    if not status:
        status = HealthCheckStatus(id=1)
        db.add(status)

    total_devices = db.query(DBCamera).count() + db.query(NVR).count() # Assumes triggering all
    
    status.is_running = True
    status.start_time = datetime.now(timezone.utc)
    status.total_cameras = total_devices # You may want to rename this column
    status.completed_cameras = 0
    db.commit()

    background_tasks.add_task(run_healthcheck_for_all)
    return {"status": "Healthcheck triggered for all devices"}


@router.get("/health/status/check")
async def check_healthcheck_status(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    # This code is already OK
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


from sqlalchemy.orm import contains_eager

# --- REFACTORING 4: Query Optimization on History Endpoint ---
@router.get("/health/history", response_class=HTMLResponse, name="health_history")
async def health_history(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
    q: Optional[str] = "",
    sort: str = "name_asc",
    page: int = Query(1, ge=1)
):
    ITEMS_PER_PAGE = get_config("items_per_page", default=25)  # Use config for items per page
    thirty_days_ago = date.today() - timedelta(days=30)

    # LANGKAH 1: Query Dasar untuk Agregasi, Penyaringan, dan Pengurutan
    base_query = db.query(
        DBCamera.id,
        DBCamera.hostname,
        func.avg(CameraDailyStats.uptime_percentage).label("average_uptime")
    ).outerjoin(
        CameraDailyStats,
        (DBCamera.id == CameraDailyStats.camera_id) & (CameraDailyStats.date >= thirty_days_ago)
    ).group_by(DBCamera.id, DBCamera.hostname)

    if q:
        base_query = base_query.filter(DBCamera.hostname.ilike(f"%{q}%"))

    # Logika pengurutan tidak berubah
    if sort == "name_asc":
        base_query = base_query.order_by(asc(DBCamera.hostname))
    elif sort == "name_desc":
        base_query = base_query.order_by(desc(DBCamera.hostname))
    elif sort == "uptime_asc":
        base_query = base_query.order_by(asc("average_uptime").nulls_last())
    elif sort == "uptime_desc":
        base_query = base_query.order_by(desc("average_uptime").nulls_last())
    else:
        base_query = base_query.order_by(asc(DBCamera.hostname))

    # LANGKAH 2: Lakukan Paginasi
    total_items = base_query.count()
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    offset = (page - 1) * ITEMS_PER_PAGE
    
    # Hasil query ini sudah dalam urutan yang benar
    paginated_results = base_query.limit(ITEMS_PER_PAGE).offset(offset).all()
    camera_ids_on_page = [item.id for item in paginated_results]
    
    # LANGKAH 3: Ambil Detail Lengkap (Tanpa order_by)
    full_camera_details = {}
    if camera_ids_on_page:
        # Hapus .order_by() dari query ini. Urutan tidak lagi penting di sini.
        query_details = db.query(DBCamera).options(
            contains_eager(DBCamera.daily_stats)
        ).filter(
            DBCamera.id.in_(camera_ids_on_page)
        ).outerjoin(
            CameraDailyStats, 
            (DBCamera.id == CameraDailyStats.camera_id) & (CameraDailyStats.date >= thirty_days_ago)
        ).all()
        # Ubah list hasil query menjadi dictionary untuk lookup yang cepat
        full_camera_details = {cam.id: cam for cam in query_details}

    # LANGKAH 4: Siapkan Konteks dengan Urutan yang Benar
    historical_data = []
    # Iterasi melalui 'paginated_results' yang sudah terurut dengan benar
    for item in paginated_results:
        cam = full_camera_details.get(item.id)
        if not cam:
            continue # Lompati jika karena suatu hal detail tidak ditemukan

        sorted_stats = sorted(cam.daily_stats, key=lambda x: x.date, reverse=True) if cam.daily_stats else []
        historical_data.append({
            "hostname": cam.hostname,
            "average_uptime": item.average_uptime,
            "stats": [{
                "date": stat.date.strftime("%Y-%m-%d"),
                "uptime_seconds": stat.total_uptime_seconds,
                "downtime_seconds": stat.total_downtime_seconds,
                "uptime_percentage": stat.uptime_percentage
            } for stat in sorted_stats]
        })

    # Pembuatan objek paginasi (tidak berubah)
    start_item = offset + 1
    end_item = min(offset + ITEMS_PER_PAGE, total_items)
    pagination_data = {
        "page": page, "per_page": ITEMS_PER_PAGE, "total": total_items,
        "total_pages": total_pages, "has_prev": page > 1, "prev_num": page - 1,
        "has_next": page < total_pages, "next_num": page + 1,
        "start_item": start_item, "end_item": end_item,
    }

    return templates.TemplateResponse("health_history.html", {
        "request": request, "historical_data": historical_data,
        "pagination": pagination_data, "search_query": q, "current_sort": sort
    })