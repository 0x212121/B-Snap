"""
Camera Groups Management Routes (Admin Only)
CRUD operations for camera groups with delete confirmation.
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.models.camera_group import CameraGroup
from app.models.camera import Camera
from app.models.nvr import NVR
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
from app.utils.template_helper import templates

router = APIRouter(tags=["Camera Groups"])


@router.get("/admin/camera-groups", response_class=HTMLResponse)
async def camera_groups_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Render the camera groups management page (admin only)."""
    return templates.TemplateResponse("camera_groups.html", {
        "request": request,
        "title": "Camera Groups",
        "admin": current_admin,
    })


@router.get("/api/camera-groups", response_class=JSONResponse)
async def get_camera_groups(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get all camera groups with usage counts."""
    groups = db.query(CameraGroup).order_by(CameraGroup.name).all()
    
    result = []
    for group in groups:
        # Count cameras and NVRs using this group
        camera_count = db.query(func.count(Camera.id)).filter(Camera.group_id == group.id).scalar()
        nvr_count = db.query(func.count(NVR.id)).filter(NVR.group_id == group.id).scalar()
        user_count = db.query(func.count(User.id)).filter(User.group_id == group.id).scalar()
        
        result.append({
            "id": group.id,
            "name": group.name,
            "camera_count": camera_count,
            "nvr_count": nvr_count,
            "user_count": user_count,
            "total_usage": camera_count + nvr_count + user_count
        })
    
    return result


@router.post("/api/camera-groups", response_class=JSONResponse)
async def create_camera_group(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Create a new camera group."""
    name = name.strip()
    
    if not name:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Group name is required."}
        )
    
    # Check for duplicate name
    existing = db.query(CameraGroup).filter(CameraGroup.name == name).first()
    if existing:
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": f"Group '{name}' already exists."}
        )
    
    try:
        group = CameraGroup(name=name)
        db.add(group)
        db.commit()
        db.refresh(group)
        
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="create_camera_group",
            target=name,
            ip=request.client.host,
            extra="via Camera Groups Management"
        )
        
        return JSONResponse(
            status_code=201,
            content={
                "status": "success",
                "message": f"Group '{name}' created successfully.",
                "group": {
                    "id": group.id,
                    "name": group.name
                }
            }
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to create group: {str(e)}"}
        )


@router.get("/api/camera-groups/{group_id}", response_class=JSONResponse)
async def get_camera_group(
    request: Request,
    group_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Get a single camera group by ID."""
    group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Count usage
    camera_count = db.query(func.count(Camera.id)).filter(Camera.group_id == group.id).scalar()
    nvr_count = db.query(func.count(NVR.id)).filter(NVR.group_id == group.id).scalar()
    user_count = db.query(func.count(User.id)).filter(User.group_id == group.id).scalar()
    
    return {
        "id": group.id,
        "name": group.name,
        "camera_count": camera_count,
        "nvr_count": nvr_count,
        "user_count": user_count
    }


@router.post("/api/camera-groups/{group_id}/update", response_class=JSONResponse)
async def update_camera_group(
    request: Request,
    group_id: int,
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Update a camera group."""
    group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    name = name.strip()
    if not name:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Group name is required."}
        )
    
    # Check for duplicate name (excluding current group)
    existing = db.query(CameraGroup).filter(
        CameraGroup.name == name,
        CameraGroup.id != group_id
    ).first()
    if existing:
        return JSONResponse(
            status_code=409,
            content={"status": "error", "message": f"Group '{name}' already exists."}
        )
    
    try:
        old_name = group.name
        group.name = name
        
        db.commit()
        db.refresh(group)
        
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="update_camera_group",
            target=old_name,
            ip=request.client.host,
            extra=f"Name changed from '{old_name}' to '{name}' via Camera Groups Management"
        )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": f"Group '{name}' updated successfully.",
                "group": {
                    "id": group.id,
                    "name": group.name
                }
            }
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to update group: {str(e)}"}
        )


@router.post("/api/camera-groups/{group_id}/delete", response_class=JSONResponse)
async def delete_camera_group(
    request: Request,
    group_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Delete a camera group (only if not in use)."""
    group = db.query(CameraGroup).filter(CameraGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    # Check if group is in use
    camera_count = db.query(func.count(Camera.id)).filter(Camera.group_id == group.id).scalar()
    nvr_count = db.query(func.count(NVR.id)).filter(NVR.group_id == group.id).scalar()
    user_count = db.query(func.count(User.id)).filter(User.group_id == group.id).scalar()
    
    if camera_count > 0 or nvr_count > 0 or user_count > 0:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"Cannot delete group '{group.name}' because it is in use by {camera_count} camera(s), {nvr_count} NVR(s), and {user_count} user(s)."
            }
        )
    
    try:
        group_name = group.name
        db.delete(group)
        db.commit()
        
        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="delete_camera_group",
            target=group_name,
            ip=request.client.host,
            extra="via Camera Groups Management"
        )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": f"Group '{group_name}' deleted successfully."
            }
        )
    except Exception as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to delete group: {str(e)}"}
        )
