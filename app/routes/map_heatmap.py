"""Authenticated historical camera downtime for the map overlay."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.user import User
from app.routes.auth import get_current_user
from app.utils.map_heatmap import calculate_downtime
from app.utils.timezone_helper import get_current_timezone

router = APIRouter(tags=["Maps"])
PERIOD_HOURS = {"24h": 24, "7d": 168, "30d": 720}


class HeatmapPoint(BaseModel):
    """A mapped camera with positive historical Offline duration."""

    camera_id: str
    lat: float
    lng: float
    downtime_seconds: int
    observed_seconds: int
    intensity: float


class HeatmapResponse(BaseModel):
    """Window, coverage, and normalized heatmap weights."""

    period: str
    start: datetime
    end: datetime
    timezone: str
    mapped_cameras: int
    incomplete_history_cameras: int
    points: list[HeatmapPoint]


def utc_now() -> datetime:
    """Return the current UTC time (also a test clock boundary)."""
    return datetime.now(UTC)


@router.get("/camera-heatmap", response_model=HeatmapResponse)
def camera_heatmap(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    period: Annotated[Literal["24h", "7d", "30d"], Query()] = "7d",
) -> HeatmapResponse:
    """Aggregate permitted Active cameras, excluding unmapped locations."""
    end = utc_now()
    start = end - timedelta(hours=PERIOD_HOURS[period])
    window_seconds = int((end - start).total_seconds())
    query = db.query(Camera).filter(
        Camera.status == "Active", Camera.latitude.isnot(None), Camera.longitude.isnot(None)
    )
    if current_user.group_id is not None:
        query = query.filter(Camera.groups.any(CameraGroup.id == current_user.group_id))
    cameras = [
        camera
        for camera in query.all()
        if isfinite(camera.latitude)
        and isfinite(camera.longitude)
        and -90 <= camera.latitude <= 90
        and -180 <= camera.longitude <= 180
        and getattr(camera, "restriction_status", None) != "Restricted"
    ]
    response = HeatmapResponse(
        period=period,
        start=start,
        end=end,
        timezone=get_current_timezone(db),
        mapped_cameras=len(cameras),
        incomplete_history_cameras=0,
        points=[],
    )
    if not cameras:
        return response

    ids = [camera.id for camera in cameras]
    # One seed state per camera plus transitions inside the window; no N+1 queries.
    ranked = (
        select(
            CameraStatusChangeLog.id,
            func.row_number()
            .over(
                partition_by=CameraStatusChangeLog.camera_id,
                order_by=(CameraStatusChangeLog.changed_at.desc(), CameraStatusChangeLog.id.desc()),
            )
            .label("rank"),
        )
        .where(CameraStatusChangeLog.camera_id.in_(ids), CameraStatusChangeLog.changed_at < start)
        .subquery()
    )
    seeds = select(ranked.c.id).where(ranked.c.rank == 1)
    logs = (
        db.query(CameraStatusChangeLog)
        .filter(
            CameraStatusChangeLog.camera_id.in_(ids),
            CameraStatusChangeLog.changed_at < end,
            or_(CameraStatusChangeLog.changed_at >= start, CameraStatusChangeLog.id.in_(seeds)),
        )
        .order_by(CameraStatusChangeLog.changed_at, CameraStatusChangeLog.id)
        .all()
    )
    by_camera = defaultdict(list)
    for log in logs:
        by_camera[log.camera_id].append(log)
    for camera in cameras:
        downtime, observed = calculate_downtime(by_camera[camera.id], start, end)
        if observed < window_seconds:
            response.incomplete_history_cameras += 1
        if downtime:
            response.points.append(
                HeatmapPoint(
                    camera_id=camera.id,
                    lat=camera.latitude,
                    lng=camera.longitude,
                    downtime_seconds=downtime,
                    observed_seconds=observed,
                    intensity=downtime / window_seconds,
                )
            )
    return response
