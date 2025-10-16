import os
import logging
import re
from typing import Optional, List, Dict, Any
from uuid import uuid4
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.utils.template_helper import templates
from app.db.database import get_db
from app.models.camera_group import CameraGroup
from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.models.snapshot_log import SnapshotLog
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist
from app.routes.auth import operator_access_required
from app.utils.audit_logger import log_audit
from app.utils.snapshot_service import take_snapshot
from app.utils.snapshot_utils import record_snapshot_metadata
from app.utils.timezone_helper import to_current_timezone

router = APIRouter(tags=["Snapshots"])

SNAPSHOT_BASE_DIR = "static"

logger = logging.getLogger("snapshot")


def _get_filtered_snapshots(db: Session, group_id: int, camera_filter: Optional[str] = None, search_query: Optional[str] = None,
                            tampered_only: bool = False, offset: int = 0, limit: int = 15) -> List[Dict[str, Any]]:
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

    if tampered_only:
        snapshot_query = snapshot_query.filter(Snapshot.is_tampered == True)

    # snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).all()
    # Lazy load snapshots
    snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).offset(offset).limit(limit).all()


    # Format data for the template
    return [
        {
            "url": f"/{SNAPSHOT_BASE_DIR}/snapshots/{s.file_path}",
            "camera": s.camera_name,
            "ip": s.camera_ip,
            "time": to_current_timezone(s.timestamp, db).strftime('%d %b %Y %H:%M:%S %Z'),
            "group": s.camera_group,
            "id": s.id,
            "file_size": int(s.file_size / 1024) if s.file_size else 0,
            "resolution": s.resolution,
            "is_tampered": s.is_tampered,
            "tamper_reason": s.tamper_reason
        }
        for s in snapshots
    ]


def is_ip_address(value: str) -> bool:
    return re.match(r"^\d{1,3}(\.\d{1,3}){3}$", value) is not None


def get_user_by_phone(db: Session, phone: str):
    return db.query(User).filter(User.phone == phone).first()


@router.post("/snap/{camera_identifier}")
def snapshot_handler(
    request: Request,
    camera_identifier: str,
    db: Session = Depends(get_db),
    user_phone: str = Query(default=None),
    current_operator: User = Depends(operator_access_required)
):
    normalized_input = camera_identifier.strip()
    camera = None
    final_user_name = "Unknown"
    extra = None

    try:
        # --- Identifikasi user ---
        if user_phone:
            user_whitelist = (
                db.query(WhatsappWhitelist)
                .filter(WhatsappWhitelist.phone_number == user_phone)
                .first()
            )
            if user_whitelist and user_whitelist.name:
                final_user_name = f"{user_whitelist.name} ({user_whitelist.phone_number})"
            else:
                final_user_name = user_phone
            extra = "via Whatsapp Bot"
        else:
            final_user_name = request.session.get("user_name", "Unknown")
            extra = "via dashboard"

        # --- Cari kamera ---
        if is_ip_address(normalized_input):
            camera = db.query(Camera).filter(Camera.ip == normalized_input).first()
        else:
            camera = (
                db.query(Camera)
                .filter(Camera.id == normalized_input)
                .first()
                or db.query(Camera)
                .filter(Camera.hostname.ilike(normalized_input))
                .first()
            )

        if not camera:
            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_failed",
                target=normalized_input,
                ip=request.client.host,
                extra=f"{extra} | reason=Camera not found"
            )
            return JSONResponse(status_code=404, content={
                "status": "error",
                "detail": "Camera not found with provided identifier"
            })

        # --- Status check ---
        if camera.status not in ["Active", "Restricted", "Maintenance"]:
            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_blocked",
                target=camera.hostname,
                ip=request.client.host,
                extra=f"{extra} | status={camera.status}"
            )
            return JSONResponse(
                status_code=403,
                content={"status": "error", "detail": f"Camera status '{camera.status}' is not allowed"}
            )

        # --- Snapshot process ---
        result = take_snapshot(camera, db)

        if result["status"] == "success":
            snapshot_log = SnapshotLog(
                id=str(uuid4()),
                camera_id=camera.id,
                camera_name=camera.hostname,
            )
            db.add(snapshot_log)

            snapshot = record_snapshot_metadata(
                db=db,
                camera_id=camera.id,
                file_path=result["file_path"],
                resolution=result.get("resolution", "N/A"),
            )
            result["snapshot_id"] = snapshot.id

            if camera.status == "Maintenance":
                camera.status = "Active"
                db.add(camera)
                db.commit()
                db.refresh(camera)
                result["status_update"] = "Camera status updated from Maintenance to Active"

            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_success",
                target=camera.hostname,
                ip=request.client.host,
                extra=f"{extra} | file={result['file_path']} | resolution={result.get('resolution', 'N/A')}"
            )
            return JSONResponse(status_code=200, content=result)
        else:
            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_failed",
                target=camera.hostname,
                ip=request.client.host,
                extra=f"{extra} | reason={result.get('message', 'unknown error')}"
            )
            return JSONResponse(status_code=500, content=result)

    except Exception as e:
        log_audit(
            db=db,
            user=final_user_name,
            action="create_snapshot_exception",
            target=camera.hostname if camera else normalized_input,
            ip=request.client.host,
            extra=f"{extra} | exception={str(e)}"
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": f"Unexpected error occurred: {str(e)}"
            }
        )


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
        logger.warning("Snapshot not found: %s", snapshot_id)
        raise HTTPException(status_code=404, detail="Snapshot not found")

    file_path = os.path.join(SNAPSHOT_BASE_DIR, "snapshots", snapshot.file_path)

    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
            logger.info("Snapshot file deleted: %s", file_path)
        except Exception as e:
            logger.error("Failed to delete snapshot file: %s", e)

    try:
        db.delete(snapshot)
        db.commit()
        logger.info("Snapshot record deleted from DB: %s", snapshot_id)

        target_time = to_current_timezone(snapshot.timestamp, db)
        formatted_time = target_time.strftime('%d %B %Y, %H:%M:%S GMT%z')

        log_audit(
            db=db,
            user=request.session["user_name"],
            action="delete_snapshot",
            target=f"{snapshot.camera_name} | {formatted_time}",
            ip=request.client.host,
            extra="via dashboard"
        )
    except Exception as e:
        logger.error("Failed to delete DB record for snapshot %s: %s", snapshot_id, e)
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

    return templates.TemplateResponse("snapshot_gallery.html", {
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
    tampered: Optional[bool] = Query(False),
    current_operator: User = Depends(operator_access_required),
    offset: int = Query(0),
    limit: int = Query(15),
):
    group_id = request.session.get("user_groupid")
    if not group_id:
        return JSONResponse(status_code=403, content={"detail": "Authentication required."})

    filtered_images = _get_filtered_snapshots(
        db,
        group_id,
        camera_filter=camera,
        search_query=q,
        tampered_only=tampered,
        offset=offset,
        limit=limit
    )

    camera_exists = False
    if camera and db.query(Camera).filter(Camera.hostname == camera).first():
        camera_exists = True

    gallery_html = templates.get_template("_gallery_grid.html").render({"images": filtered_images})
    buttons_html = templates.get_template("_action_buttons.html").render({
        "selected_camera": camera, 
        "camera_exists": camera_exists,
        'role': request.session.get("user_role")
    })

    return JSONResponse({
        'html': gallery_html,
        'buttons_html': buttons_html
    })

from app.schemas.snapshot_schema import SnapshotOut
@router.get("/snapshots/tampered", response_model=List[SnapshotOut])
def get_tampered_snapshots(request: Request, db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required),):
    return db.query(Snapshot).filter(Snapshot.is_tampered == True).all()


@router.get("/snapshots/tampered", response_class=JSONResponse)
def get_tampered_snapshots_range(
    start: str = Query(..., description="Start date in YYYY-MM-DD"),
    end: str = Query(..., description="End date in YYYY-MM-DD"),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
):
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d")
        end_date = datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    snapshots = db.query(Snapshot).filter(
        Snapshot.is_tampered == True,
        Snapshot.timestamp >= start_date,
        Snapshot.timestamp <= end_date
    ).order_by(Snapshot.timestamp.desc()).all()

    result = [{
        "id": s.id,
        "camera": s.camera_name,
        "timestamp": s.timestamp.isoformat(),
        "tamper_reason": s.tamper_reason,
        "blur_score": s.blur_score,
        "entropy_score": getattr(s, 'entropy_score', None),
    } for s in snapshots]

    return JSONResponse(content=result)