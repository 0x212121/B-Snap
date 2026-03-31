from datetime import datetime, timezone, timedelta
from typing import List, Optional, cast
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import joinedload, Session
from sqlalchemy import func, and_
from pydantic import BaseModel
from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
from app.models.snapshot import Snapshot
from app.models.user import User
from app.routes.auth import get_current_user
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.utils.timezone_helper import format_datetime_standard, format_time_with_tz_abbr, get_current_timezone, to_current_timezone, format_datetime_with_tz
from app.utils.template_helper import templates

router = APIRouter(tags=["Maps"])


def format_uptime(start_time: datetime, end_time: datetime) -> str:
    if not start_time or not end_time:
        return "N/A"
    td = end_time - start_time
    days, seconds = td.days, td.seconds
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
    ip: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    asset_no: Optional[str] = None
    status: str
    last_online: str
    uptime: str
    restricted: Optional[str] = None
    cam_group: Optional[int] = None
    user_group: Optional[str] = "N/A"
    user_group_id: Optional[int] = None
    coordinate: Optional[str] = None
    note: Optional[str] = ""

    class Config:
        from_attributes = True


@router.get("/maps")
async def maps_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    map_title = get_config("map_title", default="CCTV Maps")
    tz_name = get_current_timezone(db)
    
    # Get current time formatted with timezone abbreviation (e.g., "09:00:00 WITA")
    now_utc = datetime.now(timezone.utc)
    current_time_local = to_current_timezone(now_utc, db)
    refresh_time = format_time_with_tz_abbr(current_time_local, db)
    
    return templates.TemplateResponse("maps.html", {
        "request": request, 
        "map_title": map_title, 
        "timezone": tz_name,
        "refresh_time": refresh_time
    })


@router.get("/camera-locations", response_model=List[CameraLocation])
async def get_camera_locations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    group_id = current_user.group_id
    group_name = current_user.group.name if current_user.group else "All Groups"

    # --- Kamera utama ---
    # If user has no group (group_id is None), they can see all cameras
    query = (
        db.query(DBCamera)
        .filter(DBCamera.status.in_(["Active", "Maintenance", "Standalone"]))
        .options(joinedload(DBCamera.health))
    )
    if group_id is not None:
        query = query.filter(DBCamera.group_id == group_id)
    cameras = query.all()

    # --- Snapshot terakhir per camera ---
    snapshot_sub = (
        db.query(
            Snapshot.camera_id,
            func.max(Snapshot.timestamp).label("last_ts")
        )
        .group_by(Snapshot.camera_id)
        .subquery()
    )
    last_snapshots = (
        db.query(Snapshot)
        .join(snapshot_sub,
              and_(
                  Snapshot.camera_id == snapshot_sub.c.camera_id,
                  Snapshot.timestamp == snapshot_sub.c.last_ts
              ))
        .all()
    )
    snapshot_map = {s.camera_id: s for s in last_snapshots}

    # --- Status log terakhir per camera ---
    status_log_sub = (
        db.query(
            CameraStatusChangeLog.camera_id,
            func.max(CameraStatusChangeLog.changed_at).label("last_changed")
        )
        .group_by(CameraStatusChangeLog.camera_id)
        .subquery()
    )
    last_logs = (
        db.query(CameraStatusChangeLog)
        .join(status_log_sub,
              and_(
                  CameraStatusChangeLog.camera_id == status_log_sub.c.camera_id,
                  CameraStatusChangeLog.changed_at == status_log_sub.c.last_changed
              ))
        .all()
    )
    log_map = {log.camera_id: log for log in last_logs}

    # --- Last online log per camera ---
    last_online_sub = (
        db.query(
            CameraStatusChangeLog.camera_id,
            func.max(CameraStatusChangeLog.changed_at).label("last_online_changed")
        )
        .filter(CameraStatusChangeLog.new_status.in_(["Online", "High Latency", "Optimal Latency"]))
        .group_by(CameraStatusChangeLog.camera_id)
        .subquery()
    )
    last_online_logs = (
        db.query(CameraStatusChangeLog)
        .join(last_online_sub,
              and_(
                  CameraStatusChangeLog.camera_id == last_online_sub.c.camera_id,
                  CameraStatusChangeLog.changed_at == last_online_sub.c.last_online_changed
              ))
        .all()
    )
    last_online_map = {log.camera_id: log for log in last_online_logs}

    # --- Context ---
    now_utc = datetime.now(timezone.utc)
    current_time_local = to_current_timezone(now_utc, db)
    online_statuses = ["Online", "High Latency", "Optimal Latency"]
    tolerance = timedelta(minutes=5)

    result = []

    for cam in cameras:
        # Status default dari DB
        status_str = str(cam.status or "Unknown")
        uptime_str = "N/A"
        formatted_last_online = "Unknown"

        # Snapshot terakhir
        last_snapshot = snapshot_map.get(cam.id)
        last_snapshot_time = None
        if last_snapshot:
            ts: datetime = cast(datetime, last_snapshot.timestamp)
            last_snapshot_time = to_current_timezone(ts, db)

        # Log status terakhir
        last_status_log = log_map.get(cam.id)
        if last_status_log:
            status_str = last_status_log.new_status

        # Lock ke Maintenance jika DB kamera Maintenance
        if cam.status == "Maintenance":
            status_str = "Maintenance"

        if cam.status == "Standalone":
            status_str = "Standalone"

        # Hitung uptime berdasarkan status log
        start_time_for_calc = None
        if status_str in online_statuses:
            # cari awal periode online
            start_of_online_period_log = (
                db.query(CameraStatusChangeLog)
                .filter(
                    CameraStatusChangeLog.camera_id == cam.id,
                    CameraStatusChangeLog.new_status.in_(online_statuses),
                    CameraStatusChangeLog.previous_status.notin_(online_statuses),
                )
                .order_by(CameraStatusChangeLog.changed_at.desc())
                .first()
            )
            if start_of_online_period_log:
                start_time_for_calc = start_of_online_period_log.changed_at
        else:
            # cari awal periode offline/maintenance
            start_of_offline_period_log = (
                db.query(CameraStatusChangeLog)
                .filter(
                    CameraStatusChangeLog.camera_id == cam.id,
                    CameraStatusChangeLog.new_status.notin_(online_statuses),
                )
                .order_by(CameraStatusChangeLog.changed_at.desc())
                .first()
            )
            if start_of_offline_period_log:
                start_time_for_calc = start_of_offline_period_log.changed_at

        if start_time_for_calc:
            start_time_local = to_current_timezone(start_time_for_calc, db)
            uptime_str = format_uptime(start_time_local, current_time_local)

        # Last online
        last_online_time = None
        last_online_log = last_online_map.get(cam.id)
        if last_online_log:
            last_online_time = to_current_timezone(last_online_log.changed_at, db)

        # fallback: kalau offline tapi snapshot < 5 menit
        if (status_str not in online_statuses and last_snapshot_time and status_str != "Maintenance"):
            if (current_time_local - last_snapshot_time) <= tolerance:
                status_str = "Online"
                uptime_str = format_uptime(last_snapshot_time, current_time_local)
                last_online_time = last_snapshot_time

        if last_online_time:
            formatted_last_online = format_datetime_with_tz(last_online_time)

        restriction_status = getattr(cam, "restriction_status", None)
        coordinate = f"{cam.latitude},{cam.longitude}"

        camera_data = {
            "id": cam.id,
            "hostname": cam.hostname or "",
            "ip": cam.ip or "",
            "lat": cam.latitude,
            "lng": cam.longitude,
            "asset_no": cam.asset_no,
            "status": status_str or "Unknown",
            "last_online": formatted_last_online or "N/A",
            "uptime": uptime_str or "N/A",
            "restricted": restriction_status,
            "cam_group": cam.group_id,
            "user_group": group_name or "N/A",
            "user_group_id": group_id,
            "coordinate": coordinate if cam.latitude and cam.longitude else None,
            "note": cam.note if cam.note else "",
        }
        result.append(camera_data)

    return result
