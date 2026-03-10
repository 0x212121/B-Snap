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
from app.utils.snapshot_service import SnapshotService
from app.utils.snapshot_utils import record_snapshot_metadata
from app.utils.notification_service import NotificationService
from app.utils.timezone_helper import to_current_timezone
from app.utils.snapshot_utils import check_orphaned_snapshots

router = APIRouter(tags=["Snapshots"])

# Base directory for snapshots - must match the physical folder structure
# Files are stored at: static/snapshots/<camera_id>/<date>/<filename>
# URL access: /static/snapshots/<camera_id>/<date>/<filename>
SNAPSHOT_BASE_DIR = "static/snapshots"

logger = logging.getLogger(__name__)


def _get_filtered_snapshots(db: Session, group_id: int, camera_filter: Optional[str] = None, search_query: Optional[str] = None,
                            tampered_only: bool = False, orphaned_only: bool = False, offset: int = 0, limit: int = 15) -> List[Dict[str, Any]]:
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
    
    # Only apply orphaned filter if column exists (backward compatibility)
    if orphaned_only:
        try:
            snapshot_query = snapshot_query.filter(Snapshot.is_orphaned == True)
        except Exception:
            # Column doesn't exist yet, return empty or ignore filter
            pass

    # snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).all()
    # Lazy load snapshots
    snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).offset(offset).limit(limit).all()


    # Format data for the template
    # Note: s.file_path contains path like "<camera_id>/<date>/<filename>"
    # SNAPSHOT_BASE_DIR = "static/snapshots", so URL = /static/snapshots/<camera_id>/<date>/<filename>
    result = []
    for s in snapshots:
        # Use getattr for backward compatibility (if is_orphaned column doesn't exist yet)
        is_orphaned = getattr(s, 'is_orphaned', False)
        result.append({
            "url": f"/{SNAPSHOT_BASE_DIR}/{s.file_path}",
            "camera": s.camera_name,
            "ip": s.camera_ip,
            "time": to_current_timezone(s.timestamp, db).strftime('%d %b %Y %H:%M:%S %Z'),
            "group": s.camera_group,
            "id": s.id,
            "file_size": int(s.file_size / 1024) if s.file_size else 0,
            "resolution": s.resolution,
            "is_tampered": s.is_tampered,
            "tamper_reason": s.tamper_reason,
            "is_orphaned": is_orphaned
        })
    return result


def is_ip_address(value: str) -> bool:
    return re.match(r"^\d{1,3}(\.\d{1,3}){3}$", value) is not None


def get_user_by_phone(db: Session, phone: str):
    return db.query(User).filter(User.phone == phone).first()


@router.post("/snap/{camera_identifier}")
async def snapshot_handler(
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

        # --- Snapshot process dengan Toast Notification ---
        snapshot = await SnapshotService.capture_snapshot(
            camera_id=camera.id,
            db=db,
            triggered_by="manual"
        )

        if snapshot:
            # Update camera status jika Maintenance
            if camera.status == "Maintenance":
                camera.status = "Active"
                db.add(camera)
                db.commit()
                db.refresh(camera)

            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_success",
                target=camera.hostname,
                ip=request.client.host,
                extra=f"{extra} | file={snapshot.file_path} | resolution={snapshot.resolution}"
            )
            
            result = {
                "status": "success",
                "file_path": snapshot.file_path,
                "snapshot_id": snapshot.id,
                "resolution": snapshot.resolution,
                "status_update": "Camera status updated from Maintenance to Active" if camera.status == "Active" else None
            }
            return JSONResponse(status_code=200, content=result)
        else:
            log_audit(
                db=db,
                user=final_user_name,
                action="create_snapshot_failed",
                target=camera.hostname,
                ip=request.client.host,
                extra=f"{extra} | reason=Snapshot capture failed"
            )
            return JSONResponse(
                status_code=500, 
                content={"status": "error", "message": "Failed to capture snapshot"}
            )

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
async def delete_snapshot(
    request: Request,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Delete snapshot dengan toast notification."""
    user_name = request.session.get("user_name", "Unknown")
    
    # Gunakan SnapshotService untuk delete dengan notifikasi
    success = await SnapshotService.delete_snapshot(
        snapshot_id=snapshot_id,  # UUID string, not int
        db=db,
        user_name=user_name
    )
    
    if success:
        return JSONResponse(
            status_code=200, 
            content={"status": "success", "message": "Snapshot deleted"}
        )
    else:
        raise HTTPException(
            status_code=500, 
            detail="Failed to delete snapshot"
        )


@router.get("/snap_gallery")
def show_snapshots(request: Request, db: Session = Depends(get_db), camera: str = "", orphaned: bool = False, current_operator: User = Depends(operator_access_required)):
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
    images = _get_filtered_snapshots(db, group_id, camera_filter=camera, orphaned_only=orphaned)
    
    # Count orphaned snapshots for badge (with fallback for backward compatibility)
    try:
        if user_group.name == 'ALL':
            orphaned_count = db.query(Snapshot).filter(Snapshot.is_orphaned == True).count()
        else:
            orphaned_count = db.query(Snapshot).filter(
                Snapshot.camera_group == user_group.name, 
                Snapshot.is_orphaned == True
            ).count()
    except Exception:
        # Column doesn't exist yet, return 0
        orphaned_count = 0

    return templates.TemplateResponse("snapshot_gallery.html", {
        "request": request,
        "images": images,
        "camera_names": all_camera_names, # Use the new list of names
        "selected_camera": camera,
        "camera_exists": camera_exists, # Pass existence flag to template
        "orphaned_only": orphaned,
        "orphaned_count": orphaned_count,
    })


@router.get("/gallery-data", response_class=JSONResponse)
async def get_gallery_data(
    request: Request,
    db: Session = Depends(get_db),
    camera: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    tampered: Optional[bool] = Query(False),
    orphaned: Optional[bool] = Query(False),
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
        orphaned_only=orphaned,
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


@router.post("/snapshots/check-orphaned", response_class=JSONResponse)
async def check_orphaned_snapshots_manual(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Manually trigger orphaned snapshots check."""
    try:
        newly_orphaned, total_orphaned = check_orphaned_snapshots(db)
        return JSONResponse({
            "status": "success",
            "newly_orphaned": newly_orphaned,
            "total_orphaned": total_orphaned,
            "message": f"Found {newly_orphaned} newly orphaned snapshots. Total orphaned: {total_orphaned}"
        })
    except Exception as e:
        logger.error("[Check Orphaned] Error: %s", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )