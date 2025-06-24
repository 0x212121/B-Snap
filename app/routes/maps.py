from fastapi.templating import Jinja2Templates
from app.db.database import get_db
from fastapi import APIRouter, Depends, Request
from app.models_sql import Camera as DBCamera, User
from sqlalchemy.orm import joinedload, Session
from app.routes.auth import get_current_user
from datetime import datetime, timedelta
import pytz # Import pytz for timezone handling

router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="templates")

# Define the GMT+8 timezone using a standard IANA name
# Note: 'Etc/GMT-8' corresponds to GMT+8. The sign is inverted in the 'Etc/GMT' convention.
GMT8_TIMEZONE = pytz.timezone('Etc/GMT-8')


@router.get("/maps")
async def maps(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("maps.html", {"request": request})


@router.get("/camera-locations")
async def get_camera_locations(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    group_name = current_user.group.name if current_user.group else "N/A"
    group_id = current_user.group_id if current_user.group else "N/A"

    try:
        if group_name == "ALL":
            cameras = db.query(DBCamera).filter(DBCamera.status == "Active").options(joinedload(DBCamera.health)).all()
        else:
            cameras = db.query(DBCamera)\
                .filter(
                    (DBCamera.group_id == group_id) & (DBCamera.status == "Active")
                )\
                .options(joinedload(DBCamera.health))\
                .all()
        
        result = []
        # Get the current time in GMT+8
        current_time_gmt8 = datetime.now(GMT8_TIMEZONE) 

        for cam in cameras:
            if not cam.health:
                print(f"❌ No health record for camera {cam.hostname} ({cam.id})")

            health = cam.health
            
            uptime_str = "N/A"
            
            online_statuses = ["Online", "High Latency", "Optimal Latency"]

            if health and health.last_online:
                # --- CORE LOGIC CHANGE ---
                # 1. Take the naive datetime from DB and make it UTC-aware.
                last_online_utc = pytz.utc.localize(health.last_online)
                
                # 2. Convert the UTC-aware datetime to our target GMT+8 timezone.
                localized_last_online = last_online_utc.astimezone(GMT8_TIMEZONE)

                if health.status in online_statuses:
                    time_difference: timedelta = current_time_gmt8 - localized_last_online
                    
                    days = time_difference.days
                    seconds = time_difference.seconds
                    hours = seconds // 3600
                    minutes = (seconds % 3600) // 60
                    
                    if days > 0:
                        uptime_str = f"{days}d {hours}h {minutes}m"
                    elif hours > 0:
                        uptime_str = f"{hours}h {minutes}m"
                    else:
                        uptime_str = f"{minutes}m"
                else:
                    uptime_str = "Offline" 
            elif health and not health.last_online:
                uptime_str = "Unknown"

            # Format last_online to show in GMT+8 if it exists
            formatted_last_online = "Unknown"
            if health and health.last_online:
                # Apply the same conversion logic for displaying the timestamp
                last_online_utc_for_display = pytz.utc.localize(health.last_online)
                localized_last_online_for_display = last_online_utc_for_display.astimezone(GMT8_TIMEZONE)
                formatted_last_online = localized_last_online_for_display.strftime("%Y-%m-%d %H:%M:%S %Z%z")

            result.append({
                "id": cam.id,
                "hostname": cam.hostname,
                "ip": cam.ip,
                "lat": cam.latitude,
                "lng": cam.longitude,
                "asset_no": cam.asset_no,
                "status": health.status if health else "Unknown",
                "last_online": formatted_last_online, # Use the timezone-aware formatted string
                "uptime": uptime_str,
                "restricted": cam.status,
                "cam_group": cam.group_id,
                "user_group": group_name,
                "user_group_id": group_id,
            })
        return result

    finally:
        db.close()