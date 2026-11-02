"""Current camera/NVR group restrictions for health queries and background work."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.nvr import NVR


def camera_scope(group_id: int | None) -> ColumnElement[bool]:
    """Authorize camera membership through its association table only."""
    return true() if group_id is None else Camera.groups.any(CameraGroup.id == group_id)


def nvr_scope(group_id: int | None) -> ColumnElement[bool]:
    """NVRs retain their existing primary-group relationship."""
    return true() if group_id is None else NVR.group_id == group_id


def require_device_access(db: Session, entity_id: str, kind: str, group_id: int | None) -> None:
    """Reject inaccessible devices before starting network/background work."""
    model, clause = (
        (Camera, camera_scope(group_id)) if kind == "camera" else (NVR, nvr_scope(group_id))
    )
    if db.query(model.id).filter(model.id == entity_id).first() is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if db.query(model.id).filter(model.id == entity_id, clause).first() is None:
        raise HTTPException(status_code=403, detail="Access denied to device group")


def require_global_health_scope(group_id: int | None) -> None:
    """Global scheduled-report definitions cannot represent a restricted group."""
    if group_id is not None:
        raise HTTPException(
            status_code=403, detail="Scheduled health reports require unrestricted device access"
        )
