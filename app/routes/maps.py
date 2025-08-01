#Berkas: app/routes/maps.py

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import joinedload, Session
from pydantic import BaseModel

from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
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

    # --- PERUBAHAN LOGIKA WAKTU ---
    # Ambil waktu saat ini sekali dan konversikan ke zona waktu lokal dari database
    now_utc = datetime.now(timezone.utc)
    current_time_local = to_current_timezone(now_utc, db)
    online_statuses = ["Online", "High Latency", "Optimal Latency"]

    result = []
    for cam in cameras:
        health = cam.health
        status_str = "Unknown"
        uptime_str = "N/A"
        formatted_last_online = "Unknown"

        if health and health.last_online:
            status_str = health.status

            # Konversi waktu 'last_online' dari UTC ke zona waktu lokal
            last_online_local = to_current_timezone(health.last_online, db)
            # Gunakan fungsi pemformatan terpusat yang baru
            formatted_last_online = format_datetime_with_tz(last_online_local)

            if health.status in online_statuses:
                # Calculate uptime for online cameras
                uptime_str = format_uptime(last_online_local, current_time_local)
            else:
                # Calculate DOWNTIME duration for offline cameras
                uptime_str = format_uptime(last_online_local, current_time_local)

        restriction_status = getattr(cam, 'restriction_status', None)

        camera_data = {
            "id": cam.id,
            "hostname": cam.hostname,
            "ip": cam.ip,
            "lat": cam.latitude,
            "lng": cam.longitude,
            "asset_no": cam.asset_no,
            "status": status_str,
            "last_online": formatted_last_online, # Sekarang berisi string waktu yang sudah diformat
            "uptime": uptime_str,
            "restricted": restriction_status,
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
        }
        result.append(camera_data)

    return result