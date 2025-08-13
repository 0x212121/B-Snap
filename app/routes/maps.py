from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import joinedload, Session
from pydantic import BaseModel
from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
from app.models.snapshot import Snapshot
from app.models.user import User
from app.routes.auth import get_current_user
from app.utils.timezone_helper import to_current_timezone, format_datetime_with_tz
from app.utils.template_helper import templates

router = APIRouter(tags=["Maps"])


def format_uptime(start_time: datetime, end_time: datetime) -> str:
    """Calculate and format uptime duration from start time to now."""
    if not start_time or not end_time:
        return "N/A"
    
    time_difference = end_time - start_time
    days = time_difference.days
    seconds = time_difference.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class CameraLocation(BaseModel):
    id: str
    hostname: str
    ip: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    asset_no: str | None = None
    status: str
    last_online: str
    uptime: str
    restricted: str | None
    cam_group: int | None
    user_group: str
    user_group_id: int | None
    coordinate: str | None
    note: str | None = ""

    class Config:
        from_attributes = True


@router.get("/maps")
async def maps_page(request: Request, current_user: User = Depends(get_current_user)):
    map_title = get_config("map_title", default="CCTV Maps")
    return templates.TemplateResponse("maps.html", {"request": request, "map_title": map_title})


@router.get("/camera-locations", response_model=List[CameraLocation])
async def get_camera_locations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    group_name = current_user.group.name if current_user.group else "N/A"
    group_id = current_user.group_id if current_user.group else None

    query = db.query(DBCamera).filter(DBCamera.status == "Active").options(joinedload(DBCamera.health))
    
    if group_name != "ALL" and group_id is not None:
        query = query.filter(DBCamera.group_id == group_id)
        
    cameras = query.all()

    now_utc = datetime.now(timezone.utc)
    current_time_local = to_current_timezone(now_utc, db)
    online_statuses = ["Online", "High Latency", "Optimal Latency"]
    tolerance = timedelta(minutes=5)  # snapshot boleh dianggap online jika ≤5 menit dari now

    result = []
    for cam in cameras:
        health = cam.health
        status_str = "Unknown"
        uptime_str = "N/A"
        formatted_last_online = "Unknown"
        last_snapshot_time = None

        # Ambil snapshot terakhir kamera ini
        last_snapshot = (
            db.query(Snapshot)
            .filter(Snapshot.camera_id == cam.id)
            .order_by(Snapshot.timestamp.desc())
            .first()
        )
        if last_snapshot:
            last_snapshot_time = to_current_timezone(last_snapshot.timestamp, db)

        if health and health.last_online:
            status_str = health.status
            last_online_local = to_current_timezone(health.last_online, db)

            # Sinkronisasi ringan:
            # Kalau offline tapi snapshot baru ≤ tolerance, update last_online & status
            if status_str not in online_statuses and last_snapshot_time:
                if (current_time_local - last_snapshot_time) <= tolerance:
                    last_online_local = last_snapshot_time
                    status_str = "Online (snapshot)"
            
            formatted_last_online = format_datetime_with_tz(last_online_local)
            uptime_str = format_uptime(last_online_local, current_time_local)

        restriction_status = getattr(cam, 'restriction_status', None)
        coordinate = f"{cam.latitude},{cam.longitude}"

        camera_data = {
            "id": cam.id,
            "hostname": cam.hostname,
            "ip": cam.ip,
            "lat": cam.latitude,
            "lng": cam.longitude,
            "asset_no": cam.asset_no,
            "status": status_str,
            "last_online": formatted_last_online,
            "uptime": uptime_str,
            "last_snapshot_time": format_datetime_with_tz(last_snapshot_time) if last_snapshot_time else None,
            "restricted": restriction_status,
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
            "coordinate": coordinate,
            "note": cam.note if cam.note is not None else ""  # Use empty string if null
        }
        result.append(camera_data)

    return result