from datetime import datetime
import logging
import os
from typing import Optional, List, Dict, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, Query, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.models_sql import Camera, CameraGroup, User, Video
from app.routes.auth import operator_access_required
from app.utils.audit_logger import log_audit
from app.utils.video import record_video_and_save_db
from app.utils.timezone import format_wita


router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Base URL path for videos.
VIDEO_URL_BASE = "static/videos"
# Base filesystem path to the videos folder, assuming execution from the project root.
VIDEO_FILESYSTEM_BASE = os.path.join("app", "static", "videos")

setup_logging()
logger = logging.getLogger("snapshot")

# Timezones
SG_TZ = ZoneInfo("Asia/Makassar")
UTC_TZ = ZoneInfo("UTC")


# =============================
# Helper Functions
# =============================


def _get_filtered_videos(db: Session, group_id: int, camera_filter: Optional[str] = None, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Helper function to fetch and filter videos from the database.
    """
    user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not user_group:
        raise HTTPException(status_code=403, detail="User group not found")

    if user_group.name != 'ALL':
        video_query = db.query(Video).filter(Video.camera_group == user_group.name)
    else:
        video_query = db.query(Video)

    if camera_filter:
        video_query = video_query.filter(Video.camera_name == camera_filter)

    if search_query:
        video_query = video_query.filter(Video.camera_name.ilike(f"%{search_query}%"))

    videos = video_query.order_by(Video.timestamp.desc()).all()

    formatted_videos = []
    for v in videos:
        formatted_time = format_wita(v.timestamp)

        formatted_videos.append({
            "id": v.id,
            "url": f"/{VIDEO_URL_BASE}/{v.file_path.replace('\\', '/')}",
            "camera": v.camera_name,
            "time": formatted_time,
            "group": v.camera_group,
            "file_size": int(v.file_size / 1024) if v.file_size else 0,
            "duration": v.duration,
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
    group_id = request.session.get("user_groupid")
    if not group_id:
        return RedirectResponse(url="/login")

    user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not user_group:
        raise HTTPException(status_code=403, detail="User group not found")

    if user_group.name == 'ALL':
        all_cameras_query = db.query(Camera.hostname)
    else:
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
    db: Session = Depends(get_db),
    camera: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    current_operator: User = Depends(operator_access_required)
):
    group_id = request.session.get("user_groupid")
    if not group_id:
        return JSONResponse(status_code=403, content={"detail": "Authentication required."})

    filtered_videos = _get_filtered_videos(db, group_id, camera_filter=camera, search_query=q)
    gallery_html = templates.get_template("_video_grid.html").render({"videos": filtered_videos, "request": request})

    return JSONResponse({'html': gallery_html})


@router.delete("/videos/{video_id}", response_class=JSONResponse)
def delete_video(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db)
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        logger.warning("Video not found in DB: %s", video_id)
        raise HTTPException(status_code=404, detail="Video not found")

    path_parts = video.file_path.replace('\\', '/').split('/')
    full_file_path = os.path.join(VIDEO_FILESYSTEM_BASE, *path_parts)

    if os.path.isfile(full_file_path):
        try:
            os.remove(full_file_path)
            logger.info("Video file deleted: %s", full_file_path)
        except Exception as e:
            logger.error("Failed to delete video file %s: %s", full_file_path, e)
    else:
        logger.warning("Video file not found on disk: %s", full_file_path)

    try:
        camera_name = video.camera_name
        db.delete(video)
        db.commit()
        logger.info("Video record deleted from DB: %s", video_id)

        log_audit(
            db=db,
            user=request.session.get("user_name", "Unknown"),
            action="delete_video",
            target=camera_name,
            ip=request.client.host,
            extra="Video ID: %s" % video_id
        )
    except Exception as e:
        logger.error("Failed to delete DB record for video %s: %s", video_id, e)
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete video record from database")

    return JSONResponse(status_code=200, content={"status": "success", "message": "Video deleted successfully"})


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

    background_tasks.add_task(record_video_and_save_db, camera_id=camera_id, duration=duration)

    log_audit(
        db=db,
        user=request.session.get("user_name", "Unknown"),
        action="start_record_video",
        target=camera.hostname,
        ip=request.client.host,
        extra=f"Duration: {duration}s"
    )

    return JSONResponse(
        status_code=202,
        content={"message": f"Recording for {duration} seconds for camera {camera.hostname} has started."}
    )
