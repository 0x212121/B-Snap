from datetime import datetime, timezone, timedelta
import logging
import os
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.models.video import Video
from app.routes.auth import admin_access_required, operator_access_required
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
    """
    # If group_id is None, user has no specific group - access to all cameras
    if group_id is None:
        video_query = db.query(Video).filter(Video.deleted_at.is_(None))
    else:
        user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
        if not user_group:
            raise HTTPException(status_code=403, detail="User group not found")
        video_query = db.query(Video).filter(
            Video.camera_group == user_group.name,
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
            "url": f"/{VIDEO_URL_BASE}/{v.file_path.replace('\\', '/')}",
            "ip": v.camera_ip,
            "camera": v.camera_name,
            "time": formatted_time,
            "group": v.camera_group,
            "file_size": int(v.file_size / 1024) if v.file_size else 0,
            "duration": v.duration,
            "resolution": v.resolution,
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
        all_cameras_query = db.query(Camera.hostname).join(Camera.group).filter(CameraGroup.id == group_id)

    all_camera_names = sorted([row[0] for row in all_cameras_query.all()])

    videos_data = _get_filtered_videos(db, group_id, camera_filter=camera)

    return templates.TemplateResponse("video_gallery.html", {
        "request": request,
        "videos": videos_data,
        "camera_names": all_camera_names,
        "selected_camera": camera,
    })


@router.get("/video-gallery-data", response_class=JSONResponse)  
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
        return JSONResponse(status_code=403, content={"detail": "Authentication required."})

    offset = (page - 1) * 12
    videos = _get_filtered_videos(db, group_id, camera_filter=camera, search_query=q, offset=offset, limit=12)

    gallery_html = templates.get_template("_video_grid.html").render({"videos": videos, "request": request})
    return JSONResponse({'html': gallery_html})


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
    for video in videos_to_purge:
        try:
            # Delete file
            if video.file_path:
                path_parts = video.file_path.replace('\\', '/').split('/')
                full_file_path = os.path.join(VIDEO_FILESYSTEM_BASE, *path_parts)
                if os.path.exists(full_file_path):
                    os.remove(full_file_path)
            
            db.delete(video)
            purged_count += 1
        except Exception as e:
            logger.error("Failed to purge video %s: %s", video.id, e)
    
    if purged_count > 0:
        db.commit()
        logger.info("Purged %d soft-deleted videos older than %d days by %s", 
                    purged_count, days_old, request.session.get("user_name"))
    
    log_audit(
        db=db,
        user=request.session.get("user_name", "Unknown"),
        action="purge_deleted_videos",
        target="system",
        ip=request.client.host,
        extra=f"Purged {purged_count} videos older than {days_old} days"
    )
    
    return JSONResponse(status_code=200, content={
        "status": "success", 
        "message": f"Purged {purged_count} videos" + (f" ({retention_hold_count} skipped due to retention hold)" if retention_hold_count > 0 else ""),
        "purged_count": purged_count,
        "retention_hold_skipped": retention_hold_count,
        "days_threshold": days_old
    })


@router.post("/videos/record/{camera_id}", response_class=JSONResponse)
async def start_recording_video(
    request: Request,
    camera_id: str,
    background_tasks: BackgroundTasks,
    duration: int = Query(10, ge=5, le=60),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    background_tasks.add_task(record_video_and_save_db, request, camera_id=camera_id, duration=duration)

    log_audit(
        db=db,
        user=request.session.get("user_name", "Unknown"),
        action="start_record_video",
        target=str(camera.hostname),
        ip=request.client.host,
        extra=f"Duration: {duration}s"
    )

    return JSONResponse(
        status_code=202,
        content={"message": f"Recording for {duration} seconds for camera {camera.hostname} has started."}
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
            "file_size": int(v.file_size / 1024 / 1024) if v.file_size else 0,  # MB
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
    from app.routes.cameras import delete_camera_entity
    
    video = db.query(Video).filter(Video.id == video_id, Video.deleted_at.isnot(None)).first()
    if not video:
        raise HTTPException(status_code=404, detail="Deleted video not found")
    
    try:
        # Delete from filesystem
        if video.file_path:
            full_path = os.path.join(VIDEO_FILESYSTEM_BASE, video.file_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        
        # Delete from database
        db.delete(video)
        db.commit()
        
        log_audit(
            db=db,
            user=request.session.get("user_name", "Unknown"),
            action="purge_video",
            target=video.camera_name,
            ip=request.client.host,
            extra=f"Permanently deleted video {video_id}"
        )
        
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Video permanently deleted"}
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to purge video %s: %s", video_id, e)
        raise HTTPException(
            status_code=500,
            detail="Failed to purge video"
        )



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
