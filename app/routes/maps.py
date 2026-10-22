"""Map presentation and group-scoped camera state summaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytz

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera as DBCamera
from app.models.camera_group import CameraGroup
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.snapshot import Snapshot
from app.models.user import User
from app.routes.auth import get_current_user
from app.routes.map_heatmap import router as heatmap_router
from app.routes.map_live import router as live_router
from app.utils.map_heatmap import as_utc
from app.utils.map_live_access import map_live_enabled
from app.utils.template_helper import templates
from app.utils.timezone_helper import (
    format_datetime_with_tz,
    format_time_with_tz_abbr,
    get_current_timezone,
)

router = APIRouter(tags=["Maps"])
router.include_router(heatmap_router)
router.include_router(live_router)


def format_uptime(start_time: datetime, end_time: datetime) -> str:
    """Format elapsed camera state duration without locale-dependent values."""
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
    """Existing public map payload, including localized event time text."""

    id: str
    hostname: str
    ip: str | None = None
    lat: float | None = None
    lng: float | None = None
    asset_no: str | None = None
    status: str
    last_online: str
    uptime: str
    restricted: str | None = None
    cam_group: int | None = None
    user_group: str | None = "N/A"
    user_group_id: int | None = None
    coordinate: str | None = None
    note: str | None = ""

    class Config:
        """Allow response validation from ORM-backed attributes."""

        from_attributes = True


@router.get("/maps")
def maps_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Render the map shell using one configured-timezone lookup."""
    map_title = get_config("map_title", default="CCTV Maps")
    tz_name = get_current_timezone(db)

    now_utc = datetime.now(UTC)
    current_time_local = now_utc.astimezone(pytz.timezone(tz_name))
    refresh_time = format_time_with_tz_abbr(current_time_local)

    return templates.TemplateResponse(
        "maps.html",
        {
            "request": request,
            "map_title": map_title,
            "timezone": tz_name,
            "refresh_time": refresh_time,
            "live_view_enabled": current_user.role == "admin" and map_live_enabled(db),
        },
    )


ONLINE_STATUSES = ("Online", "High Latency", "Optimal Latency")


@router.get("/camera-locations", response_model=list[CameraLocation])
def get_camera_locations(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[CameraLocation]:
    """Return bounded map summaries in four data queries, excluding authentication."""
    group_id = current_user.group_id
    group_name = current_user.group.name if current_user.group else "All Groups"
    query = db.query(DBCamera).filter(DBCamera.status.in_(["Active", "Maintenance", "Standalone"]))
    if group_id is not None:
        query = query.filter(DBCamera.groups.any(CameraGroup.id == group_id))
    cameras = query.all()
    if not cameras:
        return []

    # Embed the authorized camera set in each history query rather than loading
    # unrelated history or expanding a potentially huge Python IN parameter list.
    permitted_ids = query.with_entities(DBCamera.id)
    snapshot_times = dict(
        db.query(Snapshot.camera_id, func.max(Snapshot.timestamp))
        .filter(Snapshot.camera_id.in_(permitted_ids))
        .group_by(Snapshot.camera_id)
        .all()
    )
    ranked = (
        db.query(
            CameraStatusChangeLog.camera_id,
            CameraStatusChangeLog.new_status,
            CameraStatusChangeLog.previous_status,
            CameraStatusChangeLog.changed_at,
            func.row_number()
            .over(
                partition_by=CameraStatusChangeLog.camera_id,
                order_by=(CameraStatusChangeLog.changed_at.desc(), CameraStatusChangeLog.id.desc()),
            )
            .label("rank"),
        )
        .filter(CameraStatusChangeLog.camera_id.in_(permitted_ids))
        .subquery()
    )
    online = ranked.c.new_status.in_(ONLINE_STATUSES)
    summaries = (
        db.query(
            ranked.c.camera_id,
            func.max(case((ranked.c.rank == 1, ranked.c.new_status))).label("status"),
            func.max(case((online, ranked.c.changed_at))).label("last_online"),
            func.max(
                case(
                    (
                        and_(online, ranked.c.previous_status.notin_(ONLINE_STATUSES)),
                        ranked.c.changed_at,
                    )
                )
            ).label("online_since"),
            func.max(
                case((ranked.c.new_status.notin_(ONLINE_STATUSES), ranked.c.changed_at))
            ).label("offline_since"),
        )
        .group_by(ranked.c.camera_id)
        .all()
    )
    history = {row.camera_id: row for row in summaries}
    # Resolve the configured zone once; all comparisons/durations stay in UTC.
    zone = pytz.timezone(get_current_timezone(db))
    now = datetime.now(UTC)
    tolerance = timedelta(minutes=5)
    result = []
    for cam in cameras:
        summary = history.get(cam.id)
        status = summary.status if summary else str(cam.status or "Unknown")
        if cam.status in ("Maintenance", "Standalone"):
            status = cam.status
        uptime = "N/A"
        last_online = summary.last_online if summary else None
        start = None
        if summary:
            start = summary.online_since if status in ONLINE_STATUSES else summary.offline_since
        if start is not None:
            uptime = format_uptime(as_utc(start), now)

        latest_snapshot = snapshot_times.get(cam.id)
        if (
            status not in ONLINE_STATUSES
            and status != "Maintenance"
            and latest_snapshot is not None
            and now - as_utc(latest_snapshot) <= tolerance
        ):
            status = "Online"
            uptime = format_uptime(as_utc(latest_snapshot), now)
            last_online = latest_snapshot
        formatted_last_online = (
            format_datetime_with_tz(as_utc(last_online).astimezone(zone))
            if last_online is not None
            else "Unknown"
        )
        coordinate = f"{cam.latitude},{cam.longitude}" if cam.latitude and cam.longitude else None
        result.append(
            CameraLocation(
                id=cam.id,
                hostname=cam.hostname or "",
                ip=cam.ip or "",
                lat=cam.latitude,
                lng=cam.longitude,
                asset_no=cam.asset_no,
                status=status,
                last_online=formatted_last_online,
                uptime=uptime,
                restricted=getattr(cam, "restriction_status", None),
                cam_group=cam.group_id,
                user_group=group_name,
                user_group_id=group_id,
                coordinate=coordinate,
                note=cam.note or "",
            )
        )
    return result
