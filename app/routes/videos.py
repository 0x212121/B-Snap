from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
import os
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import JSONResponse, ORJSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.models.video import Video
from app.routes.auth import admin_access_required, operator_access_required
from app.schemas.video import VideoRecordRequest
from app.utils.audit_logger import log_audit
from app.utils.timezone_helper import to_current_timezone, format_datetime_standard
from app.utils.video import record_video_and_save_db
from app.utils.timezone import format_wita


router = APIRouter(tags=["Videos"])
from app.utils.template_helper import templates

# Base URL path for videos.
VIDEO_URL_BASE = "static/videos"
# Base filesystem path to the videos folder, assuming execution from the project root.
VIDEO_FILESYSTEM_BASE = os.path.join("static", "videos")

setup_logging()
logger = logging.getLogger("snapshot")


# =============================
# Helpers
# =============================

def _get_filtered_videos(
    db: Session,
    group_id: int,
    camera_filter: Optional[str] = None,
    search_query: Optional[str] = None,
    offset: int = 0,
    limit: int = 12
) -> List[Dict[str, Any]]:
    """
    Helper function to fetch and filter videos from the database.
    
    NOTE: If group_id is None, user has access to all cameras (no group restriction).
    CRIT-001: Returns secure API URLs instead of direct static URLs.
    """
    # CRIT-002: Use foreign key relationship instead of camera_group name matching
    if group_id is None:
        # User has no specific group - access to all cameras
        video_query = db.query(Video).filter(Video.deleted_at.is_(None))
    else:
        # CRIT-002 FIX: Use join with Camera table to filter by group_id via foreign key
        video_query = db.query(Video).join(
            Camera, Video.camera_id == Camera.id, isouter=True
        ).filter(
            Camera.groups.any(CameraGroup.id == group_id),
            Video.deleted_at.is_(None)
        )

    if camera_filter:
        video_query = video_query.filter(Video.camera_name == camera_filter)

    if search_query:
        video_query = video_query.filter(Video.camera_name.ilike(f"%{search_query}%"))

    videos = video_query.order_by(Video.timestamp.desc()).offset(offset).limit(limit).all()

    formatted_videos = []
    for v in videos:
        formatted_time = format_datetime_standard(v.timestamp, db=db)

        formatted_videos.append({
            "id": v.id,
            # CRIT-001: Use authenticated API endpoint instead of direct static URL
            "url": f"/api/videos/secure/{v.id}",
            "thumb_url": f"/api/videos/secure/{v.id}?thumb=true",
            "detail_url": f"/api/videos/secure/{v.id}",
            "file_path": v.file_path,  # Keep for reference
            "ip": v.camera_ip,
            "camera": v.camera_name,
            "time": formatted_time,
            "group": v.camera_group,
            "file_size": int(v.file_size),
            "duration": v.duration,
            "resolution": v.resolution,
        })

    return formatted_videos


# =============================
# Routes
# =============================

@router.get("/videos", response_class=JSONResponse)
def show_videos(
    request: Request,
    db: Session = Depends(get_db),
    camera: str = "",
    current_operator: User = Depends(operator_access_required)
):
    # FIX: Always get fresh group_id from database to handle group changes without relogin
    user = db.query(User).filter(User.id == request.session.get("user_id")).first()
    group_id = user.group_id if user else request.session.get("user_groupid")
    # group_id can be None - meaning user has access to all groups
    
    # If group_id is None, user has access to all cameras
    if group_id is None:
        all_cameras_query = db.query(Camera.hostname)
    else:
        user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
        if not user_group:
            raise HTTPException(status_code=403, detail="User group not found")
        all_cameras_query = db.query(Camera.hostname).join(Camera.groups).filter(CameraGroup.id == group_id)

    all_camera_names = sorted([row[0] for row in all_cameras_query.all()])

    videos_data = _get_filtered_videos(db, group_id, camera_filter=camera)

    return templates.TemplateResponse("video_gallery.html", {
        "request": request,
        "videos": videos_data,
        "camera_names": all_camera_names,
        "selected_camera": camera,
    })


@router.get("/video-gallery-data", response_class=ORJSONResponse)
async def get_video_gallery_data(
    request: Request,
    page: int = Query(1, ge=1),
    camera: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    # FIX: Always get fresh group_id from database to handle group changes without relogin
    user = db.query(User).filter(User.id == request.session.get("user_id")).first()
    group_id = user.group_id if user else request.session.get("user_groupid")
    # group_id can be None (access to all cameras) or a specific group ID
    # Check if user is authenticated by checking user_id in session
    if not request.session.get("user_id"):
        return ORJSONResponse(status_code=403, content={"detail": "Authentication required."})

    offset = (page - 1) * 12
    videos = _get_filtered_videos(db, group_id, camera_filter=camera, search_query=q, offset=offset, limit=12)

    gallery_html = templates.get_template("_video_grid.html").render({"videos": videos, "request": request})
    return ORJSONResponse({'html': gallery_html})


@router.delete("/videos/{video_id}", response_class=JSONResponse)
def delete_video(
    request: Request,
    video_id: str,
    hard_delete: bool = Query(False, description="If True, permanently delete (admin only)"),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
):
    """Delete a video (soft delete by default, hard delete for admin)."""
    from datetime import datetime, timezone
    
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        logger.warning("Video not found in DB: %s", video_id)
        raise HTTPException(status_code=404, detail="Video not found")

    # P0-002: Check if user is admin for hard delete
    user_role = request.session.get("user_role", "")
    if hard_delete and user_role != "admin":
        raise HTTPException(status_code=403, detail="Hard delete requires admin privileges")

    try:
        camera_name = video.camera_name
        v_timestamp = video.timestamp
        
        if hard_delete:
            # P0-002: Hard delete - permanently remove file and record
            path_parts = video.file_path.replace('\\', '/').split('/')
            full_file_path = os.path.join(VIDEO_FILESYSTEM_BASE, *path_parts)

            if os.path.isfile(full_file_path):
                try:
                    os.remove(full_file_path)
                    logger.info("Video file hard deleted: %s", full_file_path)
                except Exception as e:
                    logger.error("Failed to hard delete video file %s: %s", full_file_path, e)

            db.delete(video)
            action = "hard_delete_video"
            message = "Video permanently deleted"
        else:
            # P0-002: Soft delete - mark as deleted
            video.soft_delete()
            action = "soft_delete_video"
            message = "Video moved to trash (soft deleted)"
        
        db.commit()
        logger.info("Video %s: %s", action, video_id)

        log_audit(
            db=db,
            user=request.session.get("user_name", "Unknown"),
            action=action,
            target=camera_name,
            ip=request.client.host,
            extra=(f"Video timestamp: {format_wita(v_timestamp)} | Hard delete: {hard_delete}"
                if isinstance(v_timestamp, datetime) else
                f"Hard delete: {hard_delete}"
             )
        )
    except Exception as e:
        logger.error("Failed to delete video %s: %s", video_id, e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete video")

    return JSONResponse(status_code=200, content={"status": "success", "message": message})


@router.post("/videos/{video_id}/restore", response_class=JSONResponse)
def restore_video(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
):
    """Restore a soft-deleted video."""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    if not video.is_deleted:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Video is not deleted"})
    
    try:
        video.restore()
        db.commit()
        
        log_audit(
            db=db,
            user=request.session.get("user_name", "Unknown"),
            action="restore_video",
            target=video.camera_name,
            ip=request.client.host,
            extra=f"Video ID: {video_id}"
        )
        
        return JSONResponse(status_code=200, content={"status": "success", "message": "Video restored successfully"})
    except Exception as e:
        logger.error("Failed to restore video %s: %s", video_id, e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to restore video")


@router.post("/admin/videos/purge", response_class=JSONResponse)
def purge_deleted_videos(
    request: Request,
    days_old: int = Query(30, description="Purge videos soft-deleted more than N days ago"),
    skip_retention_hold: bool = Query(True, description="Skip items with retention hold"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Admin only: Permanently delete videos that have been soft-deleted for specified days.
    
    P2-001: Items with retention_hold=True are skipped by default.
    """
    from datetime import datetime, timezone, timedelta
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
    
    # Build query
    query = db.query(Video).filter(
        Video.deleted_at.isnot(None),
        Video.deleted_at < cutoff_date
    )
    
    # P2-001: Skip retention hold items
    if skip_retention_hold:
        query = query.filter(Video.retention_hold == False)
    
    videos_to_purge = query.all()
    
    # Count retention hold items for info
    retention_hold_count = 0
    if skip_retention_hold:
        retention_hold_count = db.query(Video).filter(
            Video.deleted_at.isnot(None),
            Video.deleted_at < cutoff_date,
            Video.retention_hold == True
        ).count()
    
    purged_count = 0
    failed_count = 0
    for video in videos_to_purge:
        try:
            # Delete file (if exists - dummy data may not have files)
            if video.file_path:
                path_parts = video.file_path.replace('\\', '/').split('/')
                full_file_path = os.path.join(VIDEO_FILESYSTEM_BASE, *path_parts)
                if os.path.exists(full_file_path):
                    try:
                        os.remove(full_file_path)
                        logger.info("[PURGE] Deleted video file: %s", full_file_path)
                    except Exception as file_err:
                        logger.warning("[PURGE] Could not delete video file %s: %s", full_file_path, file_err)
                else:
                    logger.info("[PURGE] Video file not found (dummy data): %s", full_file_path)
            
            db.delete(video)
            purged_count += 1
        except Exception as e:
            failed_count += 1
            logger.error("[PURGE] Failed to purge video %s: %s", video.id, e)
    
    if purged_count > 0:
        try:
            db.commit()
        except Exception as commit_err:
            db.rollback()
            logger.error("[PURGE] Failed to commit purge: %s", commit_err)
            raise HTTPException(status_code=500, detail=f"Failed to commit purge: {str(commit_err)}")
        logger.info("Purged %d soft-deleted videos older than %d days by %s", 
                    purged_count, days_old, request.session.get("user_name"))
    
    # Log audit (non-critical)
    try:
        log_audit(
            db=db,
            user=request.session.get("user_name", "Unknown"),
            action="purge_deleted_videos",
            target="system",
            ip=request.client.host,
            extra=f"Purged {purged_count} videos older than {days_old} days"
        )
    except Exception as audit_err:
        logger.warning("[PURGE] Audit log failed (non-critical): %s", audit_err)
    
    return JSONResponse(status_code=200, content={
        "status": "success", 
        "message": f"Purged {purged_count} videos" + (f" ({retention_hold_count} skipped due to retention hold)" if retention_hold_count > 0 else ""),
        "purged_count": purged_count,
        "failed_count": failed_count,
        "retention_hold_skipped": retention_hold_count,
        "days_threshold": days_old
    })


def _authorize_video_recording(
    request: Request,
    camera: Camera,
    duration: int,
    db: Session,
    current_operator: User,
) -> None:
    """Check camera access and audit the authenticated recording command."""
    if current_operator.group_id is not None and not any(
        group.id == current_operator.group_id for group in camera.groups
    ):
        raise HTTPException(status_code=403, detail="Access denied to this camera")

    log_audit(
        db=db,
        user=current_operator.username,
        action="start_record_video",
        target=str(camera.hostname),
        ip=request.client.host if request.client else "unknown",
        extra=f"Duration: {duration}s",
    )


def _queue_video_recording(
    request: Request,
    camera: Camera,
    duration: int,
    background_tasks: BackgroundTasks,
    db: Session,
    current_operator: User,
) -> JSONResponse:
    """Authorize and schedule a background recording."""
    _authorize_video_recording(request, camera, duration, db, current_operator)
    background_tasks.add_task(
        record_video_and_save_db, request, camera_id=camera.id, duration=duration
    )
    return JSONResponse(
        status_code=202,
        content={
            "message": f"Recording for {duration} seconds for camera {camera.hostname} has started.",
            "camera_id": camera.id,
            "hostname": camera.hostname,
            "duration": duration,
        },
    )


def _resolve_record_camera(payload: VideoRecordRequest, db: Session) -> Camera:
    """Resolve an unambiguous registered hostname or IP."""
    query = db.query(Camera)
    if payload.hostname is not None:
        query = query.filter(func.lower(Camera.hostname) == payload.hostname.lower())
    else:
        query = query.filter(Camera.ip == payload.ip)

    # Check all cameras before group filtering: a duplicate IP is always ambiguous.
    cameras = query.limit(2).all()
    if not cameras:
        raise HTTPException(status_code=404, detail="Camera not found")
    if len(cameras) > 1:
        selector = "IP address" if payload.ip is not None else "hostname"
        raise HTTPException(
            status_code=409,
            detail=f"Multiple cameras match this {selector}. Recording was not started. "
            "Use a unique hostname or camera ID.",
        )
    return cameras[0]


@router.post("/api/videos/record", response_class=JSONResponse, status_code=202)
async def record_video_by_camera(
    request: Request,
    payload: VideoRecordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
) -> JSONResponse:
    """Record by exact hostname or IP in the background."""
    camera = _resolve_record_camera(payload, db)
    return _queue_video_recording(
        request, camera, payload.duration, background_tasks, db, current_operator
    )


@router.post("/api/videos/record-and-wait", response_class=JSONResponse)
async def record_video_and_wait(
    request: Request,
    payload: VideoRecordRequest,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
) -> JSONResponse:
    """Return the completed recording for API flows that deliver video to WhatsApp."""
    camera = _resolve_record_camera(payload, db)
    _authorize_video_recording(request, camera, payload.duration, db, current_operator)
    try:
        result = await record_video_and_save_db(
            request, camera_id=camera.id, duration=payload.duration,
            actor_username=current_operator.username,
        )
    except Exception as exc:
        logger.exception("Recording failed for camera %s", camera.id)
        raise HTTPException(status_code=502, detail="Video recording failed") from exc
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail=result.get("message") or "Recording failed")
    return JSONResponse(content=result)


@router.post("/videos/record/{camera_id}", response_class=JSONResponse)
async def start_recording_video(
    request: Request,
    camera_id: str,
    background_tasks: BackgroundTasks,
    duration: int = Query(10, ge=5, le=60),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
) -> JSONResponse:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    return _queue_video_recording(
        request, camera, duration, background_tasks, db, current_operator
    )



# ========== P0-002: Soft Delete Management Routes for Videos ==========

@router.get("/admin/videos/deleted", response_class=JSONResponse)
async def get_deleted_videos(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(None, description="Filter by camera name"),
    days: int = Query(0, description="Filter items older than N days (0 = all)"),
):
    """Admin only: Get list of soft-deleted videos."""
    offset = (page - 1) * limit
    
    # Build base query
    query = db.query(Video).filter(Video.deleted_at.isnot(None))
    
    # Apply camera name filter
    if search:
        query = query.filter(Video.camera_name.ilike(f"%{search}%"))
    
    # Apply days filter
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(Video.deleted_at < cutoff)
    
    total = query.count()
    
    videos = query.order_by(Video.deleted_at.desc()).offset(offset).limit(limit).all()
    
    result = []
    for v in videos:
        result.append({
            "id": v.id,
            "camera": v.camera_name,
            "deleted_at": format_datetime_standard(v.deleted_at, db=db),
            "timestamp": format_datetime_standard(v.timestamp, db=db),
            "file_path": v.file_path,
            "duration": v.duration,
            "file_size": int(v.file_size),
            "resolution": v.resolution,
            # P2-001: Retention hold fields
            "retention_hold": v.retention_hold,
            "retention_hold_reason": v.retention_hold_reason,
            "retention_hold_by": v.retention_hold_by,
            "retention_hold_at": format_datetime_standard(v.retention_hold_at, db=db) if v.retention_hold_at else None,
        })
    
    return JSONResponse({
        "videos": result,
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit
    })


@router.delete("/admin/videos/{video_id}/purge", response_class=JSONResponse)
async def purge_video(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Admin only: Permanently delete a soft-deleted video."""
    logger.info("[PURGE] Starting purge for video_id: %s", video_id)
    
    try:
        video = db.query(Video).filter(Video.id == video_id, Video.deleted_at.isnot(None)).first()
        if not video:
            logger.warning("[PURGE] Video not found or not soft-deleted: %s", video_id)
            raise HTTPException(status_code=404, detail="Deleted video not found")
        
        camera_name = video.camera_name or "Unknown"
        
        # Delete from filesystem (if file exists)
        if video.file_path:
            try:
                path_parts = video.file_path.replace('\\', '/').split('/')
                full_path = os.path.join(VIDEO_FILESYSTEM_BASE, *path_parts)
                if os.path.exists(full_path):
                    os.remove(full_path)
                    logger.info("[PURGE] Deleted video file: %s", full_path)
                else:
                    logger.info("[PURGE] Video file not found (dummy data): %s", full_path)
            except Exception as file_err:
                logger.warning("[PURGE] Could not delete video file: %s", file_err)
                # Continue to delete DB record even if file delete fails
        
        # Delete from database
        try:
            db.delete(video)
            db.commit()
            logger.info("[PURGE] Deleted video record from DB: %s", video_id)
        except Exception as db_err:
            db.rollback()
            logger.error("[PURGE] Failed to delete video from DB: %s", db_err)
            raise HTTPException(status_code=500, detail=f"Database error: {str(db_err)}")
        
        # Log audit
        try:
            log_audit(
                db=db,
                user=request.session.get("user_name", "Unknown"),
                action="purge_video",
                target=camera_name,
                ip=request.client.host,
                extra=f"Permanently deleted video {video_id}"
            )
        except Exception as audit_err:
            logger.warning("[PURGE] Audit log failed (non-critical): %s", audit_err)
        
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Video permanently deleted"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[PURGE] Unexpected error purging video %s: %s", video_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )



# CRIT-001: Secure Video Serving - Authenticated access only

@router.get("/api/videos/file/{file_path:path}")
async def serve_video_file(
    request: Request,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Serve video file through authenticated API (CRIT-001).
    
    This endpoint replaces direct static file access to enforce authentication
    and audit logging for all video access.
    """
    import mimetypes
    from fastapi.responses import FileResponse, StreamingResponse
    
    # Security: Prevent directory traversal
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=403, detail="Invalid file path")
    
    # Build full path
    full_path = os.path.join(VIDEO_FILESYSTEM_BASE, file_path)
    
    # Verify file exists and is within videos directory
    try:
        real_path = os.path.realpath(full_path)
        base_dir = os.path.realpath(VIDEO_FILESYSTEM_BASE)
        if not real_path.startswith(base_dir):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    
    if not os.path.exists(real_path) or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Log access for audit trail
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="view_video",
        target=file_path,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_path=str(request.url.path),
        request_method=request.method,
        response_status=200,
    )
    
    # Determine content type
    content_type, _ = mimetypes.guess_type(real_path)
    if not content_type:
        content_type = "video/mp4"
    
    # Serve file
    return FileResponse(
        path=real_path,
        media_type=content_type,
        filename=os.path.basename(file_path),
    )


@router.get("/api/videos/secure/{video_id}")
async def serve_video_by_id(
    request: Request,
    video_id: str,
    download: bool = Query(False, description="Set Content-Disposition to attachment for download"),
    thumb: bool = Query(False, description="Thumbnail view - minimal logging"),
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Serve video file by video ID with authentication (CRIT-001).
    
    Preferred method: Uses video ID instead of file path for better security.
    
    Args:
        download: If True, sets Content-Disposition to attachment for file download
        thumb: If True, this is a thumbnail view (minimal logging)
    """
    import mimetypes
    from fastapi.responses import FileResponse
    
    # Find video in database
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if video is soft-deleted (only admin can view deleted)
    if video.deleted_at and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied to deleted video")
    
    if not video.file_path:
        raise HTTPException(status_code=404, detail="Video file path not found")
    
    
    # Build full path
    full_path = os.path.join(VIDEO_FILESYSTEM_BASE, video.file_path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk")
    
    if thumb:
        thumb_path = str(Path(full_path).with_suffix(".jpg"))
        if os.path.exists(thumb_path):
            response = FileResponse(thumb_path, media_type="image/jpeg")
            response.headers["Cache-Control"] = "public, max-age=86400"
            return response
        else:
            response = FileResponse("static/video-placeholder.jpg", media_type="image/jpeg")
            response.headers["Cache-Control"] = "public, max-age=3600"
            return response
    
    
    # Log access (CRIT-001 compliance)
    if download:
        action = "download_video"
    else:
        action = "view_video"
    
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action=action,
        target=f"{video.camera_name}/{video_id}",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_path=str(request.url.path),
        request_method=request.method,
        response_status=200,
    )
    
    # Build filename for download
    filename = os.path.basename(video.file_path)
    if download and video.camera_name:
        # Create meaningful filename: CameraName_YYYYMMDD_HHMMSS.mp4
        from datetime import datetime, timezone
        timestamp_str = video.timestamp.strftime("%Y%m%d_%H%M%S") if video.timestamp else datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ext = os.path.splitext(filename)[1]
        safe_camera_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in video.camera_name)
        filename = f"{safe_camera_name}_{timestamp_str}{ext}"
    
    # Determine content type
    content_type, _ = mimetypes.guess_type(full_path)
    if not content_type:
        content_type = "video/mp4"
    
    return FileResponse(
        path=full_path,
        media_type=content_type,
        filename=filename if download else None,
    )


@router.post("/api/videos/gallery-view", response_class=JSONResponse)
async def log_video_gallery_view(
    request: Request,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Log video gallery batch view untuk compliance tanpa flooding (CRIT-001).
    
    Frontend memanggil endpoint ini sekali ketika load gallery page,
    daripada log setiap video individual.
    
    Request body:
    {
        "camera_filter": "Camera Name" | null,
        "video_count": 12,
        "search_query": "search term" | null
    }
    """
    camera_filter = data.get("camera_filter")
    video_count = data.get("video_count", 0)
    search_query = data.get("search_query")
    
    # Build target description
    target_parts = []
    if camera_filter:
        target_parts.append(f"camera:{camera_filter}")
    if search_query:
        target_parts.append(f"search:{search_query}")
    target_parts.append(f"count:{video_count}")
    target = " | ".join(target_parts) if target_parts else f"all_cameras:{video_count}"
    
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="video_gallery_view",
        target=target,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_path=str(request.url.path),
        request_method=request.method,
        response_status=200,
    )
    
    return JSONResponse({"status": "logged"})


# P2-001: Retention Hold Management Endpoints for Videos

@router.post("/videos/{video_id}/retention-hold", response_class=JSONResponse)
async def toggle_video_retention_hold(
    request: Request,
    video_id: str,
    enable: bool = Query(..., description="Enable or disable retention hold"),
    reason: str = Query(None, description="Reason for retention hold"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Toggle retention hold on a video to prevent purge."""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    
    user_name = request.session.get("user_name", "Unknown")
    
    if enable:
        video.retention_hold = True
        video.retention_hold_reason = reason or "Legal/Evidence hold"
        video.retention_hold_by = user_name
        video.retention_hold_at = datetime.now(timezone.utc)
        action = "retention_hold_enabled"
        message = "Retention hold applied - this video cannot be purged"
    else:
        video.retention_hold = False
        video.retention_hold_reason = None
        video.retention_hold_by = None
        video.retention_hold_at = None
        action = "retention_hold_disabled"
        message = "Retention hold removed - this video can now be purged"
    
    db.commit()
    
    log_audit(
        db=db,
        user=user_name,
        action=action,
        target=video.camera_name,
        ip=request.client.host,
        extra=f"Video {video_id} - Reason: {reason}" if enable else f"Video {video_id}"
    )
    
    return JSONResponse({
        "status": "success",
        "message": message,
        "retention_hold": video.retention_hold,
        "retention_hold_reason": video.retention_hold_reason,
        "retention_hold_by": video.retention_hold_by
    })
