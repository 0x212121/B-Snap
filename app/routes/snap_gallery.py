import logging
import re
from typing import Optional, List, Dict, Any
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.models_sql import CameraGroup, Camera, Snapshot, SnapshotLog, User
from app.routes.auth import operator_access_required
from app.utils.audit_logger import log_audit
from app.utils.snapshot_service import take_snapshot
from app.utils.snapshot_utils import record_snapshot_metadata
import os

router = APIRouter()

templates = Jinja2Templates(directory="templates")

SNAPSHOT_BASE_DIR = "static"

setup_logging()
logger = logging.getLogger("snapshot")


def _get_filtered_snapshots(db: Session, group_id: int, camera_filter: Optional[str] = None, search_query: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Helper function to query and filter snapshots from the database.
    This function now correctly fetches all snapshots, even if the camera has been deleted.
    """
    user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not user_group:
        raise HTTPException(status_code=403, detail="User group not found")

    # Base query on the Snapshot table. This ensures all snapshots are retrieved.
    if user_group.name != 'ALL':
        snapshot_query = db.query(Snapshot).filter(Snapshot.camera_group == user_group.name)
    else:
        snapshot_query = db.query(Snapshot)

    # Apply filters if provided
    if camera_filter:
        snapshot_query = snapshot_query.filter(Snapshot.camera_name == camera_filter)
    
    if search_query:
        snapshot_query = snapshot_query.filter(Snapshot.camera_name.ilike(f"%{search_query}%"))

    snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).all()

    # Format data for the template
    return [
        {
            "url": f"/{SNAPSHOT_BASE_DIR}/snapshots/{s.file_path}",
            "camera": s.camera_name,
            "ip": s.camera_ip,
            "time": s.timestamp.strftime("%Y-%m-%d %H:%M:%S WITA"),
            "group": s.camera_group,
            "id": s.id,
            "file_size": int(s.file_size / 1024) if s.file_size else 0,
            "resolution": s.resolution
        }
        for s in snapshots
    ]


def is_ip_address(value: str) -> bool:
    return re.match(r"^\d{1,3}(\.\d{1,3}){3}$", value) is not None


def get_user_by_phone(db: Session, phone: str):
    return db.query(User).filter(User.phone == phone).first()


@router.post("/snap/{camera_id_or_ip}")
def snapshot_handler(
    request: Request,
    camera_id_or_ip: str,
    db: Session = Depends(get_db),
    user_phone: str = Query(default=None),
    current_operator: User = Depends(operator_access_required)
):
    # This function remains the same. It requires an existing camera to take a snapshot.
    if is_ip_address(camera_id_or_ip):
        camera = db.query(Camera).filter(Camera.ip == camera_id_or_ip).first()
    else:
        camera = db.query(Camera).filter(Camera.hostname == camera_id_or_ip).first()

    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    result = take_snapshot(camera, db)

    if result["status"] == "success":
        snapshot_log = SnapshotLog(
            id=str(uuid4()),
            camera_name=camera.hostname
        )
        db.add(snapshot_log)

        path = result["file_path"]
        snapshot = record_snapshot_metadata(
            db=db,
            camera_id=camera.id,
            file_path=path,
            resolution=result.get("resolution", "N/A"),
        )
        result["snapshot_id"] = snapshot.id
    
    user_from_param = user_phone if user_phone else None
    if user_from_param:
        final_user_name, extra = user_from_param, "via Whatsapp Bot"  
    else:
        final_user_name, extra = request.session["user_name"], "via dashboard"
    
    log_audit(
        db=db,
        user=final_user_name,
        action="create_snapshot",
        target=camera.hostname,
        ip=request.client.host,
        extra=extra
    )
    return result


@router.delete("/snap/{snapshot_id}", response_class=JSONResponse)
def delete_snapshot(
    request: Request,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    # This function remains the same.
    snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
    if not snapshot:
        logger.warning(f"Snapshot not found: {snapshot_id}")
        raise HTTPException(status_code=404, detail="Snapshot not found")

    file_path = os.path.join(SNAPSHOT_BASE_DIR, "snapshots", snapshot.file_path)

    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
            logger.info(f"Snapshot file deleted: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete snapshot file: {e}")

    try:
        db.delete(snapshot)
        db.commit()
        logger.info(f"Snapshot record deleted from DB: {snapshot_id}")

        log_audit(
            db=db,
            user=request.session["user_name"],
            action="delete_snapshot",
            target=f"{snapshot.camera_name} | {snapshot.timestamp.strftime('%d %B %Y, %H:%M:%S WITA')}",
            ip=request.client.host,
            extra="via dashboard"
        )
    except Exception as e:
        logger.error(f"Failed to delete DB record for snapshot {snapshot_id}: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete snapshot record")

    return JSONResponse(status_code=200, content={"status": "success", "message": "Snapshot deleted"})


@router.get("/snap_gallery")
def show_snapshots(request: Request, db: Session = Depends(get_db), camera: str = "", current_operator: User = Depends(operator_access_required)):
    group_id = request.session.get("user_groupid")
    if not group_id:
        return RedirectResponse(url="/login")

    user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not user_group:
        raise HTTPException(status_code=403, detail="User group not found")

    # --- CHANGE 1: Get camera list for dropdown from snapshots ---
    # This query gets the names of only those cameras that have snapshots.
    if user_group.name != 'ALL':
        cameras_with_snapshots_query = db.query(Snapshot.camera_name).filter(Snapshot.camera_group == user_group.name)
    else:
        cameras_with_snapshots_query = db.query(Snapshot.camera_name)
    
    # Get distinct names and sort them
    camera_name_tuples = cameras_with_snapshots_query.distinct().all()
    all_camera_names = sorted([name[0] for name in camera_name_tuples])
    
    # --- CHANGE 2: Check if selected camera exists for initial load ---
    camera_exists = False
    if camera:
        if db.query(Camera).filter(Camera.hostname == camera).first():
            camera_exists = True

    # Get all snapshots for the initial view using the helper
    images = _get_filtered_snapshots(db, group_id, camera_filter=camera)

    return templates.TemplateResponse("snap_gallery.html", {
        "request": request,
        "images": images,
        "camera_names": all_camera_names, # Use the new list of names
        "selected_camera": camera,
        "camera_exists": camera_exists, # Pass existence flag to template
    })


@router.get("/gallery-data", response_class=JSONResponse)
async def get_gallery_data(
    request: Request,
    db: Session = Depends(get_db),
    camera: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    current_operator: User = Depends(operator_access_required)
):
    group_id = request.session.get("user_groupid")
    if not group_id:
        return JSONResponse(status_code=403, content={"detail": "Authentication required."})

    # Use the helper function to get the filtered snapshot data
    filtered_images = _get_filtered_snapshots(db, group_id, camera_filter=camera, search_query=q)

    # --- CHANGE 3: Check if camera exists before rendering action buttons ---
    camera_exists = False
    if camera:
        # Check if the camera exists in the Camera table to enable the snapshot button
        if db.query(Camera).filter(Camera.hostname == camera).first():
            camera_exists = True

    # Render the HTML partials with the filtered data
    gallery_html = templates.get_template("_gallery_grid.html").render({"images": filtered_images})
    buttons_html = templates.get_template("_action_buttons.html").render({
        "selected_camera": camera, 
        "camera_exists": camera_exists, # Pass the flag to the buttons template
        'role': request.session.get("user_role")
    })
    
    return JSONResponse({
        'html': gallery_html,
        'buttons_html': buttons_html
    })
