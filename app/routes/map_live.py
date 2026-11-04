"""Authenticated live camera previews for the Maps popup."""

from __future__ import annotations

import time

from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
from app.utils.camera_onvif import get_rtsp_url
from app.utils.live_snapshot import SnapshotPreview
from app.utils.live_stream import (
    BOUNDARY,
    STREAM_SECONDS,
    LivePreview,
    configured_rtsp_source,
    reserve_viewer,
)
from app.utils.map_live_access import live_policy_allows, map_live_enabled
from app.utils.public_live_access import public_live_camera, public_live_policy_allows
from app.utils.public_live_token import verify_live_grant
from app.utils.video import _hls_recording_url

router = APIRouter(tags=["Maps"])


@router.get("/maps/live/status")
def live_status(
    db: Annotated[Session, Depends(get_db)],
    _admin: Annotated[User, Depends(admin_access_required)],
) -> dict[str, bool]:
    """Let an active administrator viewer detect configuration changes."""
    return {"enabled": map_live_enabled(db)}


@router.get("/maps/cameras/{camera_id}/live")
def live_camera(
    camera_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(admin_access_required)],
) -> StreamingResponse:
    """Serve live frames without revealing camera credentials to the browser."""
    if not map_live_enabled(db):
        raise HTTPException(status_code=403, detail="Maps Live View is disabled")
    query = db.query(Camera).filter(Camera.id == camera_id)
    if current_user.group_id is not None:
        query = query.filter(Camera.groups.any(CameraGroup.id == current_user.group_id))
    camera = query.first()
    if camera is None or getattr(camera, "restriction_status", None) == "Restricted":
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.status not in {"Active", "Standalone"}:
        raise HTTPException(status_code=409, detail="Camera is not available for live preview")
    return _stream_camera(
        camera,
        request,
        db,
        actor=current_user.username,
        viewer_id=str(current_user.id),
        allowed=lambda: live_policy_allows(current_user.id),
    )


@router.get("/public/cameras/{camera_id}/live")
def public_camera_live(
    camera_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Query(max_length=2000)] = "",
) -> StreamingResponse:
    """Serve only the camera authorized by a valid, unexpired public live grant."""
    grant = verify_live_grant(camera_id, token)
    camera = public_live_camera(db, grant)
    return _stream_camera(
        camera,
        request,
        db,
        actor=f"public_live:issuer:{grant['issuer']}",
        viewer_id=str(grant["issuer"]),
        allowed=lambda: public_live_policy_allows(camera_id, token),
        expires_at=grant["expires"],
        audit_action="view_public_camera_live",
    )


def _stream_camera(
    camera: Camera,
    request: Request,
    db: Session,
    *,
    actor: str,
    viewer_id: str,
    allowed: Callable[[], bool],
    expires_at: float | None = None,
    audit_action: str = "view_camera_live",
) -> StreamingResponse:
    """Share camera source resolution, bounded playback, and cleanup for both viewers."""
    release = reserve_viewer(viewer_id)
    preview = None
    try:
        # Camera.port is also the HTTP/ONVIF port; standard web ports are not RTSP.
        rtsp_port = 554 if camera.port in {None, 80, 443} else camera.port
        snapshot_source = (camera.snapshot_url or "").strip()
        http_snapshot = urlparse(snapshot_source).scheme.lower() in {
            "http",
            "https",
        } and not _hls_recording_url(camera)
        source = (
            snapshot_source
            if http_snapshot
            else (
                _hls_recording_url(camera)
                or configured_rtsp_source(camera)
                or (get_rtsp_url(camera, rtsp_port=rtsp_port, try_acti=True) if camera.ip else None)
            )
        )
        if not source:
            raise HTTPException(status_code=502, detail="Camera live source is unavailable")
        parsed = urlparse(source)
        if parsed.scheme.lower() not in {"rtsp", "rtsps", "http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=502, detail="Camera live source is unavailable")
        duration = {}
        if expires_at is not None:
            remaining = min(STREAM_SECONDS, expires_at - time.time())
            if remaining <= 0:
                raise HTTPException(status_code=403, detail="Live link is invalid or expired")
            duration = {"seconds": remaining, "expires_at": expires_at}
        preview = (
            SnapshotPreview(
                source,
                camera.username or "",
                camera.password or "",
                release,
                allowed=allowed,
                **duration,
            )
            if http_snapshot
            else LivePreview(source, release, allowed=allowed, **duration)
        )
        first = preview.first_frame()
        if expires_at is not None and (time.time() >= expires_at or not allowed()):
            raise HTTPException(status_code=403, detail="Live link is invalid or expired")
        log_audit(
            db=db,
            user=actor,
            action=audit_action,
            target=camera.id,
            ip=request.client.host if request.client else None,
            request_path=request.url.path,
            request_method="GET",
            response_status=200,
        )
        # Audit commit releases the DB connection before the long-lived response.
        return StreamingResponse(
            preview.chunks(first),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )
    except BaseException:
        if preview is not None:
            preview.close()
        else:
            release()
        raise
