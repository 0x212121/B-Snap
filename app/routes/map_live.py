"""Authenticated live camera previews for the Maps popup."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
from app.utils.camera_onvif import get_rtsp_url
from app.utils.live_stream import BOUNDARY, LivePreview, configured_rtsp_source, reserve_viewer
from app.utils.map_live_access import live_policy_allows, map_live_enabled
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
    release = reserve_viewer(str(current_user.id))
    preview = None
    try:
        # Camera.port is also the HTTP/ONVIF port; standard web ports are not RTSP.
        rtsp_port = 554 if camera.port in {None, 80, 443} else camera.port
        source = (
            _hls_recording_url(camera)
            or configured_rtsp_source(camera)
            or (get_rtsp_url(camera, rtsp_port=rtsp_port, try_acti=True) if camera.ip else None)
        )
        if not source:
            raise HTTPException(status_code=502, detail="Camera live source is unavailable")
        parsed = urlparse(source)
        if parsed.scheme.lower() not in {"rtsp", "rtsps", "http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=502, detail="Camera live source is unavailable")
        user_id = current_user.id
        preview = LivePreview(source, release, allowed=lambda: live_policy_allows(user_id))
        first = preview.first_frame()
        log_audit(
            db=db,
            user=current_user.username,
            action="view_camera_live",
            target=camera_id,
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
            },
        )
    except BaseException:
        if preview is not None:
            preview.close()
        else:
            release()
        raise
