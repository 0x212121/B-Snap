"""Authenticated, sender-scoped camera media discovery for bot commands."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Query as CameraQuery, Session

from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.config import Configuration
from app.models.snapshot import Snapshot
from app.models.user import User
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required, user_access_required
from app.utils.map_live_access import map_live_enabled
from app.utils.phone_numbers import whatsapp_phone_variants

router = APIRouter(prefix="/api/bot/cameras", tags=["WhatsApp"])


def sender_camera_query(
    db: Session, user: User, phone: str
) -> tuple[CameraQuery, WhatsappWhitelist]:
    """Intersect token-owner and whitelist group memberships on every request."""
    entry = (
        db.query(WhatsappWhitelist)
        .filter(WhatsappWhitelist.phone_number.in_(whatsapp_phone_variants(phone)))
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=403, detail="Sender must be on the WhatsApp whitelist")
    query = db.query(Camera)
    for group_id in {user.group_id, entry.group_id} - {None}:
        query = query.filter(Camera.groups.any(CameraGroup.id == group_id))
    return query, entry


@router.get("/live-link")
def camera_live_link(
    keyword: Annotated[str, Query(min_length=1, max_length=255)],
    phone_number: Annotated[str, Query(min_length=1, max_length=100)],
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(admin_access_required)],
) -> dict:
    """Return a credential-free viewer URL; opening it requires an admin session."""
    query, entry = sender_camera_query(db, current_admin, phone_number)
    if entry.role != RoleEnum.admin:
        raise HTTPException(status_code=403, detail="Live View requires a whitelist admin")
    if not map_live_enabled(db):
        raise HTTPException(status_code=403, detail="Maps Live View is disabled")
    value = keyword.strip()
    camera = query.filter(
        or_(Camera.id == value, func.lower(Camera.hostname) == value.lower(), Camera.ip == value)
    ).first()
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found or inaccessible")
    if camera.status not in {"Active", "Standalone"}:
        raise HTTPException(status_code=409, detail="Camera is not available for live preview")
    config = db.query(Configuration).filter(Configuration.key == "app_public_url").first()
    base = (config.value if config else "").strip().rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=409, detail="Configure a valid app_public_url first")
    return {
        "camera_id": camera.id,
        "camera": camera.hostname,
        "url": f"{base}/maps/cameras/{quote(camera.id, safe='')}/live",
        "requires_login": True,
    }


@router.get("/latest-snapshots")
def latest_camera_snapshots(
    *,
    scope: Literal["group", "location"],
    keyword: Annotated[str, Query(min_length=1, max_length=255)],
    phone_number: Annotated[str, Query(min_length=1, max_length=100)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(user_access_required)],
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=50)] = 50,
) -> dict:
    """Select one latest non-deleted snapshot per accessible camera, without capture."""
    query, _entry = sender_camera_query(db, current_user, phone_number)
    value = keyword.strip().lower()
    if not value:
        raise HTTPException(status_code=422, detail="Group or location cannot be blank")
    if scope == "group":
        query = query.filter(Camera.groups.any(func.lower(CameraGroup.name) == value))
    else:
        query = query.filter(func.lower(Camera.location) == value)
    total = query.count()
    if not total:
        raise HTTPException(status_code=404, detail="No accessible cameras match this selection")
    cameras = (
        query.order_by(Camera.hostname, Camera.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    ranked = (
        db.query(
            Snapshot.id.label("id"),
            func.row_number()
            .over(
                partition_by=Snapshot.camera_id,
                order_by=(Snapshot.timestamp.desc(), Snapshot.id.desc()),
            )
            .label("rank"),
        )
        .filter(
            Snapshot.camera_id.in_([camera.id for camera in cameras]), Snapshot.deleted_at.is_(None)
        )
        .subquery()
    )
    snapshots = {
        snapshot.camera_id: snapshot
        for snapshot in db.query(Snapshot)
        .join(ranked, Snapshot.id == ranked.c.id)
        .filter(ranked.c.rank == 1)
        .all()
    }
    root = Path("static/snapshots").resolve()
    results, missing = [], []
    for camera in cameras:
        snapshot = snapshots.get(camera.id)
        path = (root / snapshot.file_path).resolve() if snapshot else None
        if path is None or not path.is_relative_to(root) or not path.is_file():
            missing.append(camera.hostname)
            continue
        timestamp = snapshot.timestamp
        if timestamp and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        results.append(
            {
                "camera_id": camera.id,
                "camera": camera.hostname,
                "snapshot_id": snapshot.id,
                "timestamp": timestamp.isoformat() if timestamp else None,
                "url": f"/api/snapshots/secure/{quote(snapshot.id, safe='')}",
            }
        )
    return {
        "results": results,
        "missing": missing,
        "total_cameras": total,
        "page": page,
        "has_more": page * per_page < total,
    }
