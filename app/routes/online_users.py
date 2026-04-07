"""Routes for online user management.

Provides API endpoints for administrators to view and manage currently online users.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.online_users import get_online_tracker
from app.utils.audit_logger import log_audit

router = APIRouter(tags=["Online Users"])


@router.get("/api/online-users")
async def get_online_users_api(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Get list of currently online users.
    
    Returns:
        JSON response with online user statistics and details.
        Only accessible by admin users.
    """
    tracker = get_online_tracker()
    stats = tracker.get_stats()
    
    return JSONResponse({
        "status": "success",
        "data": {
            "total_online": stats["total_online"],
            "by_role": stats["by_role"],
            "users": stats["users"],
        }
    })


@router.get("/api/online-users/count")
async def get_online_users_count(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Get count of currently online users (lightweight endpoint).
    
    Returns:
        JSON response with just the count for the sticky popup.
    """
    tracker = get_online_tracker()
    stats = tracker.get_stats()
    
    return JSONResponse({
        "status": "success",
        "data": {
            "count": stats["total_online"],
            "by_role": stats["by_role"],
        }
    })


@router.post("/api/online-users/{user_id}/revoke")
async def revoke_user_session(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Revoke a user's session (force logout)."""
    admin_username = request.session.get("user_name", "unknown")
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "User not found"}
        )
    
    if target_user.id == current_admin.id:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Cannot revoke your own session"}
        )
    
    target_username = target_user.username
    
    # Clear all web tokens
    target_user.web_tokens = []
    db.commit()
    
    # Remove from online tracker - sekarang pakai user_id based removal
    tracker = get_online_tracker()
    tracker.remove_user_by_id(user_id)  # Ganti dari loop session ke method baru
    
    log_audit(
        db=db,
        user=admin_username,
        action="revoke_user_session",
        target=f"user:{target_username}",
        ip=request.client.host if request.client else "unknown",
        extra=f"Admin revoked session for user {target_username} (ID: {user_id})"
    )
    
    return JSONResponse({
        "status": "success",
        "message": f"Session revoked for user {target_username}",
        "data": {
            "user_id": user_id,
            "username": target_username,
            "revoked_by": admin_username
        }
    })