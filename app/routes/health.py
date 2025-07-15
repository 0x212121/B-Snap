from collections import Counter, defaultdict
from datetime import datetime, timedelta, date, timezone
import math
from typing import Optional
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import asc, desc, func, union_all, literal_column, select
from sqlalchemy.orm import Session, contains_eager
from app.core.config import get_config
from app.db.database import get_db
from app.routes.auth import operator_access_required
from app.utils.health_check import run_healthcheck_for_all, run_healthcheck_for_camera, run_healthcheck_for_nvr
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from app.models_sql import NVR, CameraDailyStats, CameraHealth as Health, Camera as DBCamera, HealthCheckStatus, User, CameraStatusChangeLog
import pytz
from app.utils.template_helper import templates

router = APIRouter()
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

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

class HealthStatusResponse(BaseModel):
    statuses: list[DeviceHealthStatus]
    camera_online_count: int
    nvr_online_count: int
    camera_offline_count: int
    nvr_offline_count: int

def _get_all_devices_health_data(db: Session) -> list:
    camera_q = select(
        DBCamera.id, DBCamera.hostname, DBCamera.ip,
        DBCamera.status.label("dev_status"),
        literal_column("'Camera'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, DBCamera.id == Health.camera_id)

    nvr_q = select(
        NVR.id, NVR.hostname, NVR.ip,
        NVR.status.label("dev_status"),
        literal_column("'NVR'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, NVR.id == Health.nvr_id)

    unified_construct = union_all(camera_q, nvr_q).alias("unified_health")
    final_query = select(unified_construct).order_by(asc(unified_construct.c.hostname))
    return db.execute(final_query).all()

@router.get("/health", response_class=HTMLResponse)
async def health_monitor_page(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    return templates.TemplateResponse("health.html", {
        "request": request,
        "initial_data": HealthStatusResponse(
            statuses=[], camera_online_count=0, nvr_online_count=0,
            camera_offline_count=0, nvr_offline_count=0
        ).json()
    })

@router.get("/health/status", response_model=HealthStatusResponse)
async def get_health_status_api(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    try:
        all_devices = _get_all_devices_health_data(db)
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
    status = db.query(HealthCheckStatus).get(1)
    if not status:
        status = HealthCheckStatus(id=1)
        db.add(status)

    total_devices = db.query(DBCamera).count() + db.query(NVR).count()
    status.is_running = True
    status.start_time = datetime.now(timezone.utc)
    status.total_cameras = total_devices
    status.completed_cameras = 0
    db.commit()

    background_tasks.add_task(run_healthcheck_for_all)
    return {"status": "Healthcheck triggered for all devices"}

@router.get("/health/status/check")
async def check_healthcheck_status(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
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


def format_duration(seconds: int) -> str:
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    elif seconds >= 60:
        return f"{seconds // 60}m"
    else:
        return f"{seconds}s"
    

@router.get("/health/history", response_class=HTMLResponse, name="health_history")
async def health_history(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
    q: Optional[str] = "",
    sort: str = "name_asc",
    page: int = Query(1, ge=1)
):
    ITEMS_PER_PAGE = get_config("items_per_page", default=25)
    thirty_days_ago = date.today() - timedelta(days=30)

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

    total_items = base_query.count()
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE)
    offset = (page - 1) * ITEMS_PER_PAGE
    paginated_results = base_query.limit(ITEMS_PER_PAGE).offset(offset).all()
    camera_ids_on_page = [item.id for item in paginated_results]

    full_camera_details = {}
    if camera_ids_on_page:
        query_details = db.query(DBCamera).options(
            contains_eager(DBCamera.daily_stats)
        ).filter(
            DBCamera.id.in_(camera_ids_on_page)
        ).outerjoin(
            CameraDailyStats, 
            (DBCamera.id == CameraDailyStats.camera_id) & (CameraDailyStats.date >= thirty_days_ago)
        ).all()
        full_camera_details = {cam.id: cam for cam in query_details}

    historical_data = []
    for item in paginated_results:
        cam = full_camera_details.get(item.id)
        if not cam:
            continue

        sorted_stats = sorted(cam.daily_stats, key=lambda x: x.date, reverse=True) if cam.daily_stats else []

        offline_map = defaultdict(list)
        offline_logs = db.query(CameraStatusChangeLog).filter(
            CameraStatusChangeLog.camera_id == cam.id,
            CameraStatusChangeLog.previous_status == "Offline",
            CameraStatusChangeLog.changed_at >= thirty_days_ago
        ).order_by(CameraStatusChangeLog.changed_at).all()

        for log in offline_logs:
            duration_secs = log.duration_since_last_change
            utc_dt = log.changed_at.replace(tzinfo=timezone.utc) if log.changed_at.tzinfo is None else log.changed_at.astimezone(timezone.utc)
            local_dt = utc_dt.astimezone(wita_tz)

            date_str = local_dt.date().isoformat()
            time_str = local_dt.strftime("%H:%M")

            if duration_secs:
                duration_str = format_duration(duration_secs)
                duration_text = f"({duration_str})"
            else:
                duration_text = ""

            # Simpan informasi waktu dan durasi sejak event sebelumnya
            offline_map[(cam.id, date_str)].append({
                "time": time_str,
                "duration_since_last_change": duration_secs,
                "duration_text": f"({duration_str})" if duration_str else ""
            })

        historical_data.append({
            "hostname": cam.hostname,
            "average_uptime": item.average_uptime,
            "stats": [{
                "date": stat.date.strftime("%Y-%m-%d"),
                "uptime_seconds": stat.total_uptime_seconds,
                "downtime_seconds": stat.total_downtime_seconds,
                "uptime_percentage": stat.uptime_percentage,
                "offline_times": offline_map.get((cam.id, stat.date.isoformat()), []),
                
            } for stat in sorted_stats]
        })

    pagination_data = {
        "page": page, "per_page": ITEMS_PER_PAGE, "total": total_items,
        "total_pages": total_pages, "has_prev": page > 1, "prev_num": page - 1,
        "has_next": page < total_pages, "next_num": page + 1,
        "start_item": offset + 1, "end_item": min(offset + ITEMS_PER_PAGE, total_items),
    }

    return templates.TemplateResponse("health_history.html", {
        "request": request,
        "historical_data": historical_data,
        "pagination": pagination_data,
        "search_query": q,
        "current_sort": sort
    })
