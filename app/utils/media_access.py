"""Group authorization shared by authenticated footage endpoints."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.video import Video


def restrict_media_query(
    query: Query, model: type[Snapshot] | type[Video], group_id: int | None
) -> Query:
    """Apply current camera membership to media lists, including bulk mutations."""
    if group_id is None:
        return query
    camera_ids = select(Camera.id).where(Camera.groups.any(CameraGroup.id == group_id))
    return query.filter(model.camera_id.in_(camera_ids))


def require_camera_group_access(db: Session, camera_id: str | None, group_id: int | None) -> None:
    """Reject footage outside the user's current camera group, including orphans."""
    if group_id is None:
        return
    accessible = (
        db.query(Camera.id)
        .filter(Camera.id == camera_id, Camera.groups.any(CameraGroup.id == group_id))
        .first()
    )
    if accessible is None:
        raise HTTPException(status_code=403, detail="Access denied to media group")
