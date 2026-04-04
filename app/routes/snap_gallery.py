import os
import logging
import re
from typing import Optional, List, Dict, Any
from uuid import uuid4
from datetime import datetime, timezone, timedelta
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
from app.routes.auth import admin_access_required, operator_access_required
from app.utils.audit_logger import log_audit
from app.utils.snapshot_service import SnapshotService
from app.utils.snapshot_utils import record_snapshot_metadata
from app.utils.notification_service import NotificationService
from app.utils.timezone_helper import to_current_timezone, format_datetime_standard

router = APIRouter(tags=["Snapshots"])

# Base directory for snapshots - must match the physical folder structure
# Files are stored at: static/snapshots/<camera_id>/<date>/<filename>
# URL access: /static/snapshots/<camera_id>/<date>/<filename>
SNAPSHOT_BASE_DIR = "static/snapshots"

logger = logging.getLogger(__name__)


def _get_filtered_snapshots(db: Session, group_id: Optional[int], camera_filter: Optional[str] = None, search_query: Optional[str] = None,
                            tampered_only: bool = False, offset: int = 0, limit: int = 15, 
                            include_deleted: bool = False) -> List[Dict[str, Any]]:
    """
    Helper function to query and filter snapshots from the database.
    This function now correctly fetches all snapshots, even if the camera has been deleted.
    
    NOTE: If group_id is None, user has access to all cameras (no group restriction).
    """
    # Base query on the Snapshot table
    if group_id is None:
        # User has no specific group - access to all cameras
        snapshot_query = db.query(Snapshot)
    else:
        user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
        if not user_group:
            raise HTTPException(status_code=403, detail="User group not found")
        # Filter by user's group
        snapshot_query = db.query(Snapshot).filter(Snapshot.camera_group == user_group.name)

    # P0-002: Filter out soft-deleted snapshots unless explicitly requested
    if not include_deleted:
        snapshot_query = snapshot_query.filter(Snapshot.deleted_at.is_(None))

    # Apply filters if provided
    if camera_filter:
        snapshot_query = snapshot_query.filter(Snapshot.camera_name == camera_filter)
    
    if search_query:
        snapshot_query = snapshot_query.filter(Snapshot.camera_name.ilike(f"%{search_query}%"))

    if tampered_only:
        snapshot_query = snapshot_query.filter(Snapshot.is_tampered == True)

    # Lazy load snapshots
    snapshots = snapshot_query.order_by(Snapshot.timestamp.desc()).offset(offset).limit(limit).all()

    # Format data for the template
    # Note: s.file_path contains path like "<camera_id>/<date>/<filename>"
    # SNAPSHOT_BASE_DIR = "static/snapshots", so URL = /static/snapshots/<camera_id>/<date>/<filename>
    result = []
    for s in snapshots:
        # Use getattr for backward compatibility (if is_orphaned column doesn't exist yet)
        is_orphaned = getattr(s, 'is_orphaned', False)
        # P0-002: Include soft delete status
        is_deleted = getattr(s, 'is_deleted', False)
        result.append({
            # P2-004: Use authenticated API endpoint instead of direct static URL
            # thumb=true untuk gallery view (minimal logging)
            "url": f"/api/snapshots/secure/{s.id}?thumb=true",
            "detail_url": f"/api/snapshots/secure/{s.id}",  # For modal view (full logging)
            "file_path": s.file_path,  # Keep for reference
            "camera": s.camera_name,
            "ip": s.camera_ip,
            "time": format_datetime_standard(s.timestamp, db=db),
            "group": s.camera_group,
            "id": s.id,
            "file_size": int(s.file_size / 1024) if s.file_size else 0,
            "resolution": s.resolution,
            "is_tampered": s.is_tampered,
            "tamper_reason": s.tamper_reason,
            "is_orphaned": is_orphaned,
            "is_deleted": is_deleted,  # P0-002
            "retention_hold": getattr(s, 'retention_hold', False)  # P2-003
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
def show_snapshots(request: Request, db: Session = Depends(get_db), camera: str = "", current_operator: User = Depends(operator_access_required)):
    group_id = request.session.get("user_groupid")
    # group_id can be None - meaning user has access to all groups
    
    # --- CHANGE 1: Get camera list for dropdown from snapshots ---
    # This query gets the names of only those cameras that have snapshots.
    # If group_id is None, user has access to all cameras (no group restriction)
    if group_id is None:
        cameras_with_snapshots_query = db.query(Snapshot.camera_name)
    else:
        user_group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
        if not user_group:
            raise HTTPException(status_code=403, detail="User group not found")
        cameras_with_snapshots_query = db.query(Snapshot.camera_name).filter(Snapshot.camera_group == user_group.name)
    
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
    # group_id can be None (access to all cameras) or a specific group ID
    # Check if user is authenticated by checking user_id in session
    if not request.session.get("user_id"):
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


# ========== P0-002: Soft Delete Management Routes ==========

@router.get("/admin/snapshots/deleted", response_class=JSONResponse)
async def get_deleted_snapshots(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(None, description="Filter by camera name"),
    days: int = Query(0, description="Filter items older than N days (0 = all)"),
):
    """Admin only: Get list of soft-deleted snapshots."""
    offset = (page - 1) * limit
    
    # Build base query
    query = db.query(Snapshot).filter(Snapshot.deleted_at.isnot(None))
    
    # Apply camera name filter
    if search:
        query = query.filter(Snapshot.camera_name.ilike(f"%{search}%"))
    
    # Apply days filter
    if days and days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(Snapshot.deleted_at < cutoff)
    
    total = query.count()
    
    snapshots = query.order_by(Snapshot.deleted_at.desc()).offset(offset).limit(limit).all()
    
    result = []
    for s in snapshots:
        result.append({
            "id": s.id,
            "camera": s.camera_name,
            "deleted_at": format_datetime_standard(s.deleted_at, db=db),
            "timestamp": format_datetime_standard(s.timestamp, db=db),
            "file_path": s.file_path,
            "file_hash": s.file_hash,
            "file_size": int(s.file_size / 1024) if s.file_size else 0,
            "resolution": s.resolution,
            "is_tampered": s.is_tampered,
            # P2-001: Retention hold fields
            "retention_hold": s.retention_hold,
            "retention_hold_reason": s.retention_hold_reason,
            "retention_hold_by": s.retention_hold_by,
            "retention_hold_at": format_datetime_standard(s.retention_hold_at, db=db) if s.retention_hold_at else None,
        })
    
    return JSONResponse({
        "snapshots": result,
        "total": total,
        "page": page,
        "total_pages": (total + limit - 1) // limit
    })


@router.post("/snap/{snapshot_id}/restore", response_class=JSONResponse)
async def restore_snapshot(
    request: Request,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Restore a soft-deleted snapshot."""
    from app.utils.snapshot_service import SnapshotService
    
    user_name = request.session.get("user_name", "Unknown")
    success = await SnapshotService.restore_snapshot(
        snapshot_id=snapshot_id,
        db=db,
        user_name=user_name
    )
    
    if success:
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Snapshot restored successfully"}
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Failed to restore snapshot (may not be deleted or not found)"
        )


@router.delete("/admin/snapshots/{snapshot_id}/purge", response_class=JSONResponse)
async def purge_snapshot(
    request: Request,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Admin only: Permanently delete a soft-deleted snapshot."""
    from app.utils.snapshot_service import SnapshotService
    
    user_name = request.session.get("user_name", "Unknown")
    success = await SnapshotService.delete_snapshot(
        snapshot_id=snapshot_id,
        db=db,
        user_name=user_name,
        hard_delete=True
    )
    
    if success:
        return JSONResponse(
            status_code=200,
            content={"status": "success", "message": "Snapshot permanently deleted"}
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Failed to purge snapshot"
        )


@router.post("/admin/snapshots/purge", response_class=JSONResponse)
async def purge_old_deleted_snapshots(
    request: Request,
    days_old: int = Query(30, description="Purge snapshots soft-deleted more than N days ago"),
    skip_retention_hold: bool = Query(True, description="Skip items with retention hold"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Admin only: Permanently delete snapshots that have been soft-deleted for specified days.
    
    P2-001: Items with retention_hold=True are skipped by default.
    """
    from app.utils.snapshot_service import SnapshotService
    
    user_name = request.session.get("user_name", "Unknown")
    
    # P2-001: Check for retention hold items
    if skip_retention_hold:
        from app.models.snapshot import Snapshot
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)
        retention_hold_count = db.query(Snapshot).filter(
            Snapshot.deleted_at.isnot(None),
            Snapshot.deleted_at < cutoff,
            Snapshot.retention_hold == True
        ).count()
    else:
        retention_hold_count = 0
    
    purged_count = SnapshotService.purge_deleted_snapshots(
        db=db,
        days_old=days_old,
        user_name=user_name,
        skip_retention_hold=skip_retention_hold
    )
    
    return JSONResponse({
        "status": "success",
        "message": f"Purged {purged_count} snapshots" + (f" ({retention_hold_count} skipped due to retention hold)" if retention_hold_count > 0 else ""),
        "purged_count": purged_count,
        "retention_hold_skipped": retention_hold_count,
        "days_threshold": days_old
    })


@router.get("/snap/{snapshot_id}/verify", response_class=JSONResponse)
async def verify_snapshot_integrity(
    request: Request,
    snapshot_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Verify the integrity of a snapshot using its stored hash."""
    snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    if not snapshot.file_hash:
        return JSONResponse({
            "status": "warning",
            "message": "No hash stored for this snapshot (legacy data)",
            "integrity_verified": None
        })
    
    is_valid = snapshot.verify_integrity()
    
    return JSONResponse({
        "status": "success" if is_valid else "failed",
        "message": "Integrity verified" if is_valid else "Integrity check FAILED - file may be corrupted or tampered",
        "integrity_verified": is_valid,
        "stored_hash": snapshot.file_hash,
        "snapshot_id": snapshot_id,
        "camera": snapshot.camera_name
    })


@router.get("/admin/trash")
async def admin_trash_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Admin Trash Management page - view and manage soft-deleted snapshots/videos."""
    return templates.TemplateResponse("admin_trash.html", {"request": request})



# P2-001: Retention Hold Management Endpoints

@router.post("/snap/{snapshot_id}/retention-hold", response_class=JSONResponse)
async def toggle_snapshot_retention_hold(
    request: Request,
    snapshot_id: str,
    enable: bool = Query(..., description="Enable or disable retention hold"),
    reason: str = Query(None, description="Reason for retention hold"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Toggle retention hold on a snapshot to prevent purge."""
    snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    user_name = request.session.get("user_name", "Unknown")
    
    if enable:
        snapshot.retention_hold = True
        snapshot.retention_hold_reason = reason or "Legal/Evidence hold"
        snapshot.retention_hold_by = user_name
        snapshot.retention_hold_at = datetime.now(timezone.utc)
        action = "retention_hold_enabled"
        message = "Retention hold applied - this snapshot cannot be purged"
    else:
        snapshot.retention_hold = False
        snapshot.retention_hold_reason = None
        snapshot.retention_hold_by = None
        snapshot.retention_hold_at = None
        action = "retention_hold_disabled"
        message = "Retention hold removed - this snapshot can now be purged"
    
    db.commit()
    
    log_audit(
        db=db,
        user=user_name,
        action=action,
        target=snapshot.camera_name,
        ip=request.client.host,
        extra=f"Snapshot {snapshot_id} - Reason: {reason}" if enable else f"Snapshot {snapshot_id}"
    )
    
    return JSONResponse({
        "status": "success",
        "message": message,
        "retention_hold": snapshot.retention_hold,
        "retention_hold_reason": snapshot.retention_hold_reason,
        "retention_hold_by": snapshot.retention_hold_by
    })



# P2-004: Secure Snapshot Serving - Authenticated access only

@router.get("/api/snapshots/file/{file_path:path}")
async def serve_snapshot_file(
    request: Request,
    file_path: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Serve snapshot file through authenticated API (P2-004).
    
    This endpoint replaces direct static file access to enforce authentication
    and audit logging for all snapshot access.
    """
    import mimetypes
    from fastapi.responses import FileResponse, StreamingResponse
    
    # Security: Prevent directory traversal
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=403, detail="Invalid file path")
    
    # Build full path
    full_path = os.path.join(SNAPSHOT_BASE_DIR, file_path)
    
    # Verify file exists and is within snapshots directory
    try:
        real_path = os.path.realpath(full_path)
        base_dir = os.path.realpath(SNAPSHOT_BASE_DIR)
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
        action="view_snapshot",
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
        content_type = "image/jpeg"
    
    # Serve file
    return FileResponse(
        path=real_path,
        media_type=content_type,
        filename=os.path.basename(file_path),
    )


@router.get("/api/snapshots/secure/{snapshot_id}")
async def serve_snapshot_by_id(
    request: Request,
    snapshot_id: str,
    download: bool = Query(False, description="Set Content-Disposition to attachment for download"),
    thumb: bool = Query(False, description="Gallery thumbnail view - minimal logging"),
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Serve snapshot file by snapshot ID with authentication (P2-004).
    
    Preferred method: Uses snapshot ID instead of file path for better security.
    
    Args:
        download: If True, sets Content-Disposition to attachment for file download
        thumb: If True, this is a gallery thumbnail view (minimal logging to prevent flooding)
    """
    import mimetypes
    from fastapi.responses import FileResponse
    
    # Find snapshot in database
    snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    # Check if snapshot is soft-deleted (only admin can view deleted)
    if snapshot.deleted_at and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied to deleted snapshot")
    
    if not snapshot.file_path:
        raise HTTPException(status_code=404, detail="Snapshot file path not found")
    
    # Build full path
    full_path = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Snapshot file not found on disk")
    
    # Log access (P2-004 compliance)
    # thumb=true: Gallery browsing - log sebagai gallery_view (batch context)
    # thumb=false atau download: Individual view - log sebagai view_snapshot
    if download:
        action = "download_snapshot"
    elif thumb:
        action = "gallery_thumbnail"  # Minimal logging untuk gallery browsing
    else:
        action = "view_snapshot"  # Full detail view
    
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action=action,
        target=f"{snapshot.camera_name}/{snapshot_id}",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_path=str(request.url.path),
        request_method=request.method,
        response_status=200,
    )
    
    # Build filename for download
    filename = os.path.basename(snapshot.file_path)
    if download and snapshot.camera_name:
        # Create meaningful filename: CameraName_YYYYMMDD_HHMMSS.jpg
        from datetime import datetime
        timestamp_str = snapshot.timestamp.strftime("%Y%m%d_%H%M%S") if snapshot.timestamp else datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = os.path.splitext(filename)[1]
        safe_camera_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in snapshot.camera_name)
        filename = f"{safe_camera_name}_{timestamp_str}{ext}"
    
    # Determine content type
    content_type, _ = mimetypes.guess_type(full_path)
    if not content_type:
        content_type = "image/jpeg"
    
    return FileResponse(
        path=full_path,
        media_type=content_type,
        filename=filename if download else None,
    )



@router.post("/api/snapshots/gallery-view", response_class=JSONResponse)
async def log_gallery_view(
    request: Request,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(operator_access_required)
):
    """Log gallery batch view untuk compliance tanpa flooding (P2-004).
    
    Frontend memanggil endpoint ini sekali ketika load gallery page,
    daripada log setiap gambar individual.
    
    Request body:
    {
        "camera_filter": "Camera Name" | null,
        "snapshot_count": 15,
        "search_query": "search term" | null
    }
    """
    camera_filter = data.get("camera_filter")
    snapshot_count = data.get("snapshot_count", 0)
    search_query = data.get("search_query")
    
    # Build target description
    target_parts = []
    if camera_filter:
        target_parts.append(f"camera:{camera_filter}")
    if search_query:
        target_parts.append(f"search:{search_query}")
    target_parts.append(f"count:{snapshot_count}")
    target = " | ".join(target_parts) if target_parts else f"all_cameras:{snapshot_count}"
    
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="gallery_view",  # Batch action untuk compliance
        target=target,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_path=str(request.url.path),
        request_method=request.method,
        response_status=200,
    )
    
    return JSONResponse({"status": "logged"})
