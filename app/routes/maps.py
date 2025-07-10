# app/routes/maps.py

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from pydantic import BaseModel
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload, Session

from app.core.config import get_config
from app.db.database import get_db
from app.models_sql import Camera as DBCamera, User
from app.routes.auth import get_current_user

router = APIRouter(
    tags=["Maps & Cameras"]
)

templates = Jinja2Templates(directory="templates")

WITA_TIMEZONE = ZoneInfo("Asia/Makassar")

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
    restricted: str | None # PERBAIKAN: Diubah agar bisa menerima None jika tidak ada status restriksi
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
    
    result = []
    current_time_wita = datetime.now(WITA_TIMEZONE)
    online_statuses = ["Online", "High Latency", "Optimal Latency"]

    for cam in cameras:
        health = cam.health
        status_str = "Unknown"
        uptime_str = "N/A"
        formatted_last_online = "Unknown"

        if health and health.last_online:
            status_str = health.status
            
            # --- PERBAIKAN KRUSIAL UNTUK TIMEZONE ---
            # Cek dulu apakah datetime dari DB 'naive' (tanpa tz) atau 'aware' (dengan tz)
            last_online_db = health.last_online
            if last_online_db.tzinfo is None:
                # Jika naive, anggap sebagai UTC lalu konversi ke WITA
                last_online_wita = last_online_db.replace(tzinfo=ZoneInfo("UTC")).astimezone(WITA_TIMEZONE)
            else:
                # Jika sudah aware, langsung konversi ke WITA
                last_online_wita = last_online_db.astimezone(WITA_TIMEZONE)

            formatted_last_online = last_online_wita.strftime("%Y-%m-%d %H:%M:%S %Z")

            if health.status in online_statuses:
                uptime_str = format_uptime(last_online_wita, current_time_wita)
            else:
                uptime_str = "Offline"
        
        # --- PERBAIKAN LOGIKA UNTUK 'restricted' ---
        # Ganti 'cam.status' dengan field yang benar untuk status restriksi.
        # Jika tidak ada field khusus, Anda bisa gunakan logika lain atau set ke None.
        # Contoh: `cam.restriction_status` atau `cam.access_level`
        # Untuk sementara, kita set sebagai None jika tidak ada fieldnya.
        restriction_status = getattr(cam, 'restriction_status', None)

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
            "restricted": restriction_status,
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
        }
        result.append(camera_data)
        
    return result