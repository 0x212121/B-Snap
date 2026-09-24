from datetime import datetime, timezone, timedelta
from typing import List, Optional, cast
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import joinedload, Session
from sqlalchemy import func, and_
from pydantic import BaseModel

from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.user import User
from app.routes.auth import get_current_user
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.utils.timezone_helper import (
    format_time_with_tz_abbr,
    get_current_timezone,
    to_current_timezone,
    format_datetime_with_tz
)
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

    # =========================
    # GET CAMERAS
    # =========================
    query = (
        db.query(DBCamera)
        .filter(DBCamera.status.in_(["Active", "Maintenance", "Standalone"]))
        .options(joinedload(DBCamera.health))
    )
    if group_id is not None:
        query = query.filter(DBCamera.groups.any(CameraGroup.id == group_id))

    cameras = query.all()
    if not cameras:
        return []

    camera_ids = [cam.id for cam in cameras]

    # =========================
    # SNAPSHOT (LATEST)
    # =========================
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

    # =========================
    # 🚀 FIX N+1 HERE
    # =========================
    all_logs = (
        db.query(CameraStatusChangeLog)
        .filter(CameraStatusChangeLog.camera_id.in_(camera_ids))
        .order_by(CameraStatusChangeLog.changed_at.desc())
        .all()
    )

    logs_by_camera = defaultdict(list)
    for log in all_logs:
        logs_by_camera[log.camera_id].append(log)

    # =========================
    # LAST STATUS & ONLINE
    # =========================
    online_statuses = ["Online", "High Latency", "Optimal Latency"]

    last_status_map = {}
    last_online_map = {}

    for cam_id, logs in logs_by_camera.items():
        if logs:
            last_status_map[cam_id] = logs[0]

        for log in logs:
            if log.new_status in online_statuses:
                last_online_map[cam_id] = log
                break

    # =========================
    # MAIN PROCESS
    # =========================
    now_utc = datetime.now(timezone.utc)
    current_time_local = to_current_timezone(now_utc, db)
    tolerance = timedelta(minutes=5)

    result = []

    for cam in cameras:
        status_str = str(cam.status or "Unknown")
        uptime_str = "N/A"
        formatted_last_online = "Unknown"

        # snapshot
        last_snapshot = snapshot_map.get(cam.id)
        last_snapshot_time = None
        if last_snapshot:
            ts = cast(datetime, last_snapshot.timestamp)
            last_snapshot_time = to_current_timezone(ts, db)

        # last status
        if cam.id in last_status_map:
            status_str = last_status_map[cam.id].new_status

        if cam.status == "Maintenance":
            status_str = "Maintenance"
        if cam.status == "Standalone":
            status_str = "Standalone"

        # uptime (NO QUERY)
        logs = logs_by_camera.get(cam.id, [])
        start_time_for_calc = None

        if status_str in online_statuses:
            for log in logs:
                if (
                    log.new_status in online_statuses and
                    log.previous_status not in online_statuses
                ):
                    start_time_for_calc = log.changed_at
                    break
        else:
            for log in logs:
                if log.new_status not in online_statuses:
                    start_time_for_calc = log.changed_at
                    break

        if start_time_for_calc:
            start_local = to_current_timezone(start_time_for_calc, db)
            uptime_str = format_uptime(start_local, current_time_local)

        # last online
        last_online_time = None
        if cam.id in last_online_map:
            last_online_time = to_current_timezone(
                last_online_map[cam.id].changed_at, db
            )

        # fallback snapshot
        if (
            status_str not in online_statuses
            and last_snapshot_time
            and status_str != "Maintenance"
        ):
            if (current_time_local - last_snapshot_time) <= tolerance:
                status_str = "Online"
                uptime_str = format_uptime(last_snapshot_time, current_time_local)
                last_online_time = last_snapshot_time

        if last_online_time:
            formatted_last_online = format_datetime_with_tz(last_online_time)

        coordinate = f"{cam.latitude},{cam.longitude}"

        result.append({
            "id": cam.id,
            "hostname": cam.hostname or "",
            "ip": cam.ip or "",
            "lat": cam.latitude,
            "lng": cam.longitude,
            "asset_no": cam.asset_no,
            "status": status_str,
            "last_online": formatted_last_online,
            "uptime": uptime_str,
            "restricted": getattr(cam, "restriction_status", None),
            "cam_group": cam.group_id,
            "user_group": group_name,
            "user_group_id": group_id,
            "coordinate": coordinate if cam.latitude and cam.longitude else None,
            "note": cam.note or "",
        })

    return result
