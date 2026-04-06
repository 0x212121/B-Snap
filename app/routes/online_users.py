"""Routes for online user management.

Provides API endpoints for administrators to view currently online users.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.online_users import get_online_tracker
from app.utils.template_helper import templates

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
