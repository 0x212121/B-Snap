from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import logging
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
import pytz
from sqlalchemy import asc, union_all, literal_column, select, and_
from sqlalchemy.orm import Session
from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.routes.auth import operator_access_required
from app.utils.healthcheck import run_healthcheck_for_all, run_healthcheck_for_camera, run_healthcheck_for_nvr
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from app.models.nvr import NVR
from app.models.health import CameraHealth as Health
from app.models.camera import Camera as DBCamera
from app.models.health_check_status import HealthCheckStatus
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.user import User
from app.utils.template_helper import templates
from app.utils.timezone_helper import get_current_timezone, to_current_timezone

router = APIRouter(tags=["Health Check"])

setup_logging()
logger = logging.getLogger("healthcheck")

class DeviceHealthStatus(BaseModel):
    id: str
    hostname: str
    ip: str
    dev_status: str | None
    type: str
    status: str | None
    latency: float | None
    checked_at: str | None = Field(None, alias="checked")
    last_online_at: str | None = Field(None, alias="last_online")
    status_changed_at: str | None

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
    ).join(Health, DBCamera.id == Health.camera_id).where(
        and_(
            DBCamera.ip.isnot(None),
            DBCamera.ip != ''
        )
    )

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
    
    logger.info(f"Current Timezone: {get_current_timezone(db)}")
    tz_name = get_current_timezone(db)
    return templates.TemplateResponse("health.html", {
        "request": request,
        "initial_data": HealthStatusResponse(
            statuses=[], camera_online_count=0, nvr_online_count=0,
            camera_offline_count=0, nvr_offline_count=0
        ).json(),
        "server_timezone": get_current_timezone(db),
        "server_timezone_label": pytz.timezone(tz_name).tzname(datetime.now())
    })

@router.get("/health/status", response_model=HealthStatusResponse)
async def get_health_status_api(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    try:
        all_devices = _get_all_devices_health_data(db)
        counts = Counter((dev.type, dev.status) for dev in all_devices)

        # Count online/offline
        camera_online = counts.get(("Camera", "Online"), 0) + counts.get(("Camera", "High Latency"), 0)
        nvr_online = counts.get(("NVR", "Online"), 0) + counts.get(("NVR", "High Latency"), 0)
        camera_offline = counts.get(("Camera", "Offline"), 0)
        nvr_offline = counts.get(("NVR", "Offline"), 0)

        formatted_devices = []
        for dev in all_devices:
            dev_dict = dict(dev._mapping)
            # Convert timestamps to ISO with timezone
            for ts_field in ["checked", "last_online", "status_changed_at"]:
                if dev_dict.get(ts_field):
                    # Biarkan format ISO (dengan offset) agar JS bisa parse dengan akurat
                    dev_dict[ts_field] = to_current_timezone(dev_dict[ts_field], db).isoformat()
                else:
                    dev_dict[ts_field] = None
            formatted_devices.append(dev_dict)

        # NEW: Inject server timezone (e.g. Asia/Makassar) dan nama label
        tz_name = get_current_timezone(db)
        return {
            "statuses": formatted_devices,
            "camera_online_count": camera_online,
            "nvr_online_count": nvr_online,
            "camera_offline_count": camera_offline,
            "nvr_offline_count": nvr_offline,
            "server_timezone": tz_name,
            "server_timezone_label": pytz.timezone(tz_name).tzname(datetime.now())
        }

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
    

@router.get("/health/history", response_class=JSONResponse)
async def get_camera_health_history(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    # pagination kamera
    base_query = db.query(DBCamera)
    total_cameras = base_query.count()
    paginated_results = base_query.offset((page - 1) * page_size).limit(page_size).all()

    if not paginated_results:
        return {"total": 0, "page": page, "page_size": page_size, "items": []}

    camera_ids_on_page = [cam.id for cam in paginated_results]
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    # ambil semua offline logs sekaligus (HAPUS N+1)
    offline_logs = db.query(CameraStatusChangeLog).filter(
        CameraStatusChangeLog.camera_id.in_(camera_ids_on_page),
        CameraStatusChangeLog.previous_status == "Offline",
        CameraStatusChangeLog.changed_at >= thirty_days_ago
    ).order_by(CameraStatusChangeLog.changed_at).all()

    # group logs by (camera_id, date)
    logs_by_camera = defaultdict(lambda: defaultdict(list))
    for log in offline_logs:
        local_dt = to_current_timezone(log.changed_at, db)
        date_str = local_dt.date().isoformat()
        time_str = local_dt.strftime("%H:%M %z")

        duration_secs = log.duration_since_last_change or 0
        duration_str = format_duration(duration_secs) if duration_secs > 0 else ""
        duration_text = f"({duration_str})" if duration_str else ""

        logs_by_camera[log.camera_id][date_str].append({
            "time": time_str,
            "duration_since_last_change": duration_secs,
            "duration_text": duration_text
        })

    items = []
    for cam in paginated_results:
        sorted_stats = sorted(cam.daily_stats, key=lambda x: x.date, reverse=True) if cam.daily_stats else []

        items.append({
            "camera": {
                "id": cam.id,
                "hostname": cam.hostname,
                "name": cam.name,
                "group_id": cam.group_id,
            },
            "history": [
                {
                    "date": stat.date.isoformat(),
                    "uptime_percentage": stat.uptime_percentage,
                    "downtime_minutes": stat.downtime_minutes,
                    "offline_times": logs_by_camera.get(cam.id, {}).get(stat.date.isoformat(), [])
                }
                for stat in sorted_stats
            ],
        })

    return {
        "total": total_cameras,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
