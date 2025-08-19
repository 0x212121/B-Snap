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
from app.models.camera_status_change_log import CameraStatusChangeLog
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
    tolerance = timedelta(minutes=5)

    result = []

    for cam in cameras:
        status_str = "Unknown"
        uptime_str = "N/A"
        formatted_last_online = "Unknown"
        last_snapshot_time = None

        last_snapshot = (
            db.query(Snapshot)
            .filter(Snapshot.camera_id == cam.id)
            .order_by(Snapshot.timestamp.desc())
            .first()
        )
        if last_snapshot:
            last_snapshot_time = to_current_timezone(last_snapshot.timestamp, db)

        last_status_log = (
            db.query(CameraStatusChangeLog)
            .filter(CameraStatusChangeLog.camera_id == cam.id)
            .order_by(CameraStatusChangeLog.changed_at.desc())
            .first()
        )

        if last_status_log:
            status_str = last_status_log.new_status
            
            # --- START: NEW AND CORRECTED UPTIME LOGIC ---

            start_time_for_calc = None
            
            if status_str in online_statuses:
                # If currently online, find the last time it transitioned FROM an OFFLINE/UNKNOWN state TO an ONLINE state.
                # This is the true start of the current uptime period.
                start_of_online_period_log = (
                    db.query(CameraStatusChangeLog)
                    .filter(
                        CameraStatusChangeLog.camera_id == cam.id,
                        CameraStatusChangeLog.new_status.in_(online_statuses),
                        CameraStatusChangeLog.previous_status.notin_(online_statuses)
                    )
                    .order_by(CameraStatusChangeLog.changed_at.desc())
                    .first()
                )
                if start_of_online_period_log:
                    start_time_for_calc = start_of_online_period_log.changed_at
                else:
                    # Fallback for cameras that have always been online (no transition log exists)
                    first_online_log = db.query(CameraStatusChangeLog).filter(
                        CameraStatusChangeLog.camera_id == cam.id,
                        CameraStatusChangeLog.new_status.in_(online_statuses)
                    ).order_by(CameraStatusChangeLog.changed_at.asc()).first()
                    if first_online_log:
                        start_time_for_calc = first_online_log.changed_at

            else: # It's offline
                # If currently offline, find the last time it WENT offline.
                start_of_offline_period_log = (
                    db.query(CameraStatusChangeLog)
                    .filter(
                        CameraStatusChangeLog.camera_id == cam.id,
                        CameraStatusChangeLog.new_status.notin_(online_statuses)
                    )
                    .order_by(CameraStatusChangeLog.changed_at.desc())
                    .first()
                )
                if start_of_offline_period_log:
                    start_time_for_calc = start_of_offline_period_log.changed_at
            
            if start_time_for_calc:
                start_time_local = to_current_timezone(start_time_for_calc, db)
                uptime_str = format_uptime(start_time_local, current_time_local)

            # Find the absolute last time it was online for the 'Last Online' field
            last_online_log = (
                db.query(CameraStatusChangeLog)
                .filter(
                    CameraStatusChangeLog.camera_id == cam.id,
                    CameraStatusChangeLog.new_status.in_(online_statuses)
                )
                .order_by(CameraStatusChangeLog.changed_at.desc())
                .first()
            )
            
            last_online_time = None
            if last_online_log:
                last_online_time = to_current_timezone(last_online_log.changed_at, db)

            if status_str not in online_statuses and last_snapshot_time:
                if (current_time_local - last_snapshot_time) <= tolerance:
                    status_str = "Online"
                    uptime_str = format_uptime(last_snapshot_time, current_time_local)
                    last_online_time = last_snapshot_time

            if last_online_time:
                formatted_last_online = format_datetime_with_tz(last_online_time)

            # --- END: NEW AND CORRECTED LOGIC ---

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
            "restricted": restriction_status,
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
            "coordinate": coordinate,
            "note": cam.note if cam.note is not None else ""
        }
        result.append(camera_data)

    return result