"""Admin management of camera archive recording, independent of WhatsApp."""

from __future__ import annotations

import shutil

from datetime import UTC, datetime
from math import ceil
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Query as ORMQuery, Session
from starlette.responses import Response

from app.core.config import get_config
from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.recording_schedule import CameraRecordingState, RecordingSchedule
from app.models.user import User
from app.routes.auth import admin_access_required
from app.schemas.recording_schedule import RecordingSchedulePayload, as_utc, next_clip_run
from app.utils.audit_logger import log_audit
from app.utils.media_access import require_camera_group_access
from app.utils.template_helper import templates
from app.utils.timezone_helper import get_current_timezone
from app.utils.video import STATIC_VIDEO_DIR

router = APIRouter(prefix="/admin/recording-schedules", tags=["Recording Schedules"])
Database = Annotated[Session, Depends(get_db)]
Administrator = Annotated[User, Depends(admin_access_required)]


def accessible_cameras(db: Session, user: User) -> ORMQuery:
    """Apply current association-based camera membership to schedule management."""
    query = db.query(Camera)
    if user.group_id is not None:
        query = query.filter(Camera.groups.any(CameraGroup.id == user.group_id))
    return query


def require_schedule(db: Session, schedule_id: str, user: User) -> RecordingSchedule:
    """Authorize the existing camera before reading or mutating a schedule."""
    row = db.get(RecordingSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recording schedule not found")
    require_camera_group_access(db, row.camera_id, user.group_id)
    return row


@router.get("")
def recording_page(request: Request, current_admin: Administrator) -> Response:
    """Render the recording schedule workspace."""
    return templates.TemplateResponse(
        "recording_schedules.html",
        {"request": request, "title": "Recording Schedules", "admin_id": current_admin.id},
    )


@router.get("/api")
def list_schedules(
    db: Database,
    current_admin: Administrator,
    camera_id: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict:
    """List settings, UTC timestamps and lease-aware status with complete pagination."""
    cameras = accessible_cameras(db, current_admin).order_by(Camera.hostname).all()
    camera_names = {camera.id: camera.hostname for camera in cameras}
    query = db.query(RecordingSchedule).filter(RecordingSchedule.camera_id.in_(camera_names))
    if camera_id:
        require_camera_group_access(db, camera_id, current_admin.group_id)
        query = query.filter(RecordingSchedule.camera_id == camera_id)
    total = query.count()
    pages = max(1, ceil(total / page_size))
    page = min(page, pages)
    rows = (
        query.order_by(RecordingSchedule.name, RecordingSchedule.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    states = {
        state.camera_id: state
        for state in db.query(CameraRecordingState)
        .filter(CameraRecordingState.camera_id.in_([row.camera_id for row in rows]))
        .all()
    }
    items = []
    now = datetime.now(UTC)
    timezone_name = get_current_timezone(db)
    for row in rows:
        item = {key: getattr(row, key) for key in RecordingSchedulePayload.model_fields}
        state = states.get(row.camera_id)
        next_run = row.next_run_at
        if (
            row.enabled
            and row.mode == "clips"
            and row.cron
            and row.next_run_timezone != timezone_name
        ):
            next_run = next_clip_run(
                RecordingSchedulePayload.model_validate(item), now, timezone_name
            )
        leased = (
            state and state.lease_owner and state.lease_until and as_utc(state.lease_until) > now
        )
        status = "scheduled" if row.enabled else "disabled"
        if leased:
            if state.schedule_id == row.id:
                status = state.status
            elif row.enabled:
                status = "camera_busy"
        elif row.enabled and state and state.schedule_id == row.id:
            if state.last_error:
                status = "error"
            elif state.status in {"starting", "recording"}:
                status = "interrupted"
        items.append(
            {
                **item,
                "id": row.id,
                "camera_name": camera_names[row.camera_id],
                "status": status,
                "last_error": state.last_error if state and state.schedule_id == row.id else None,
                "last_recorded_at": (
                    as_utc(state.last_recorded_at).isoformat()
                    if state and state.last_recorded_at
                    else None
                ),
                "next_run_at": (
                    as_utc(next_run).isoformat()
                    if row.enabled and row.mode == "clips" and next_run
                    else None
                ),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "timezone": timezone_name,
        "cameras": [{"id": camera.id, "name": camera.hostname} for camera in cameras],
        "retention": {
            "mode": get_config("retention_video_mode", "age"),
            "days": get_config("retention_video_days", 7),
            "count": get_config("max_video_per_camera", 20),
        },
        "storage": {
            "free_mb": shutil.disk_usage(STATIC_VIDEO_DIR).free // (1024 * 1024),
            "reserve_mb": int(get_config("recording_min_free_mb", 1024)),
            "max_cameras": int(get_config("recording_max_cameras", 4)),
        },
    }


def save_schedule(
    request: Request,
    payload: RecordingSchedulePayload,
    db: Session,
    user: User,
    row: RecordingSchedule | None = None,
) -> dict:
    """Save validated settings and audit the exact schedule that will execute."""
    camera = db.get(Camera, payload.camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    require_camera_group_access(db, camera.id, user.group_id)
    action = "update_recording_schedule" if row else "create_recording_schedule"
    if row is None:
        row = RecordingSchedule(id=str(uuid4()))
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    now = datetime.now(UTC)
    row.owner_id = user.id
    row.updated_at = now
    row.next_run_at = None
    row.next_run_timezone = get_current_timezone(db)
    if payload.mode == "clips" and payload.enabled:
        row.next_run_at = (
            next_clip_run(payload, now, row.next_run_timezone) if payload.cron else now
        )
    db.flush()
    log_audit(
        db,
        user=user.username,
        action=action,
        target=row.id,
        ip=request.client.host if request.client else None,
        extra={"camera_id": camera.id, "mode": row.mode, "enabled": row.enabled},
    )
    return {"id": row.id, "status": "success"}


@router.post("/api", status_code=201)
def create_schedule(
    request: Request,
    payload: RecordingSchedulePayload,
    db: Database,
    current_admin: Administrator,
) -> dict:
    """Create a schedule using the current administrator's execution permissions."""
    return save_schedule(request, payload, db, current_admin)


@router.put("/api/{schedule_id}")
def update_schedule(
    schedule_id: str,
    request: Request,
    payload: RecordingSchedulePayload,
    db: Database,
    current_admin: Administrator,
) -> dict:
    """Apply an edit to a schedule and let the recorder finalize the previous session."""
    return save_schedule(
        request, payload, db, current_admin, require_schedule(db, schedule_id, current_admin)
    )


@router.delete("/api/{schedule_id}")
def delete_schedule(
    schedule_id: str,
    request: Request,
    db: Database,
    current_admin: Administrator,
) -> dict:
    """Stop future recording while retaining all previously archived evidence."""
    row = require_schedule(db, schedule_id, current_admin)
    db.delete(row)
    log_audit(
        db,
        user=current_admin.username,
        action="delete_recording_schedule",
        target=schedule_id,
        ip=request.client.host if request.client else None,
        extra={"camera_id": row.camera_id},
    )
    return {"status": "success"}
