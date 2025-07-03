# app/routes/maps.py

# PERUBAHAN 1: Menggunakan zoneinfo dari pustaka standar Python (lebih modern dari pytz)
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

# Impor Pydantic untuk membuat model respons
from pydantic import BaseModel
from typing import List

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload, Session

from app.core.config import get_config
from app.db.database import get_db
from app.models_sql import Camera as DBCamera, User
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/maps",  # Menambahkan prefix untuk semua rute di file ini
    tags=["Maps & Cameras"] # Mengelompokkan API di dokumentasi
)

templates = Jinja2Templates(directory="templates")

# Menggunakan nama zona waktu IANA yang lebih deskriptif untuk GMT+8 (WITA)
# Ini lebih mudah dibaca daripada 'Etc/GMT-8'
WITA_TIMEZONE = ZoneInfo("Asia/Makassar")

# --- Helper Function untuk kebersihan kode ---
def format_uptime(start_time: datetime, end_time: datetime) -> str:
    """Menghitung dan memformat durasi uptime dari waktu mulai hingga sekarang."""
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

# PERUBAHAN 2: Mendefinisikan Response Model menggunakan Pydantic
# Ini memastikan output API selalu konsisten dan terdokumentasi dengan baik.
class CameraLocation(BaseModel):
    id: int
    hostname: str
    ip: str
    lat: float
    lng: float
    asset_no: str | None = None
    status: str
    last_online: str
    uptime: str
    restricted: str
    cam_group: int | None
    user_group: str
    user_group_id: int | None

    class Config:
        # orm_mode = True # Memungkinkan model untuk membaca data dari objek ORM
        model_config = {
            "from_attributes": True
        }


@router.get("/")
async def maps_page(request: Request, current_user: User = Depends(get_current_user)):
    map_title = get_config("map_title", default="CCTV Maps")
    return templates.TemplateResponse("maps.html", {"request": request, "map_title": map_title})


# Menggunakan response_model untuk memastikan output sesuai dengan model CameraLocation
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

        if health:
            status_str = health.status
            if health.last_online:
                # Asumsi: health.last_online disimpan di DB sebagai naive datetime dalam UTC
                # 1. Buat datetime menjadi aware dengan zona waktu aslinya (UTC)
                last_online_utc = health.last_online.replace(tzinfo=ZoneInfo("UTC"))
                # 2. Konversi ke zona waktu target (WITA)
                last_online_wita = last_online_utc.astimezone(WITA_TIMEZONE)
                
                formatted_last_online = last_online_wita.strftime("%Y-%m-%d %H:%M:%S %Z")

                if health.status in online_statuses:
                    # PERUBAHAN 3: Menggunakan helper function
                    uptime_str = format_uptime(last_online_wita, current_time_wita)
                else:
                    uptime_str = "Offline"

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
            "restricted": cam.status,
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
        }
        result.append(camera_data)
        
    return result