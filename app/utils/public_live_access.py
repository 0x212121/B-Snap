"""Recheck issuing identities and camera access for public live grants."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.utils.map_live_access import map_live_enabled
from app.utils.public_live_token import verify_live_grant


def public_live_camera(db: Session, grant: dict[str, Any]) -> Camera:
    """Fail closed if the issuing admin/sender or their camera access was revoked."""
    owner = db.query(User).filter(User.id == grant["issuer"]).first()
    sender = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.id == grant["sender"]).first()
    if not owner or owner.role != "admin" or not sender or sender.role != RoleEnum.admin:
        raise HTTPException(status_code=403, detail="Live link access was revoked")
    if not map_live_enabled(db):
        raise HTTPException(status_code=403, detail="Maps Live View is disabled")
    query = db.query(Camera).filter(Camera.id == grant["camera"])
    for group_id in {owner.group_id, sender.group_id} - {None}:
        query = query.filter(Camera.groups.any(CameraGroup.id == group_id))
    camera = query.first()
    if camera is None or camera.status not in {"Active", "Standalone"}:
        raise HTTPException(status_code=403, detail="Live link camera is unavailable")
    return camera


def public_live_policy_allows(camera_id: str, token: str) -> bool:
    """Revalidate grant expiry and live access using a short database session."""
    try:
        grant = verify_live_grant(camera_id, token)
        with SessionLocal() as db:
            public_live_camera(db, grant)
        return True
    except Exception:
        return False
