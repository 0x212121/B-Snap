"""Notification routes for B-Snap.

Provides API endpoints for managing notifications:
- Get user's notifications
- Mark notifications as read
- Delete notifications
- Get unread count
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.notification import Notification
from app.routes.auth import user_access_required
from app.models.user import User

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/")
async def get_notifications(
    request: Request,
    unread_only: bool = Query(False, description="Only return unread notifications"),
    limit: int = Query(50, ge=1, le=100, description="Number of notifications to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Get notifications for the current user."""
    user_id = current_user.id
    
    query = db.query(Notification).filter(
        (Notification.user_id == user_id) | (Notification.user_id.is_(None))
    )
    
    if unread_only:
        query = query.filter(Notification.is_read.is_(None))
    
    total = query.count()
    notifications = query.order_by(
        Notification.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return {
        "total": total,
        "unread": db.query(Notification).filter(
            (Notification.user_id == user_id) | (Notification.user_id.is_(None))
        ).filter(Notification.is_read.is_(None)).count(),
        "notifications": [n.to_dict() for n in notifications],
    }


@router.get("/unread-count")
async def get_unread_count(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Get count of unread notifications."""
    user_id = current_user.id
    
    count = db.query(Notification).filter(
        (Notification.user_id == user_id) | (Notification.user_id.is_(None))
    ).filter(Notification.is_read.is_(None)).count()
    
    return {"unread_count": count}


@router.post("/{notification_id}/read")
async def mark_as_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Mark a notification as read."""
    from datetime import datetime, timezone
    
    notification = db.query(Notification).filter(
        Notification.id == notification_id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    # Check permission (only owner or broadcast can be marked)
    user_id = current_user.id
    if notification.user_id is not None and notification.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    notification.is_read = datetime.now(timezone.utc)
    db.commit()
    
    return {"success": True}


@router.post("/mark-all-read")
async def mark_all_as_read(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Mark all notifications as read for the current user."""
    from datetime import datetime, timezone
    
    user_id = current_user.id
    
    db.query(Notification).filter(
        (Notification.user_id == user_id) | (Notification.user_id.is_(None))
    ).filter(Notification.is_read.is_(None)).update({
        Notification.is_read: datetime.now(timezone.utc)
    })
    
    db.commit()
    
    return {"success": True}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Delete a notification."""
    notification = db.query(Notification).filter(
        Notification.id == notification_id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    # Check permission
    user_id = current_user.id
    if notification.user_id is not None and notification.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    db.delete(notification)
    db.commit()
    
    return {"success": True}


@router.delete("/")
async def clear_all_notifications(
    request: Request,
    read_only: bool = Query(True, description="Only clear read notifications"),
    db: Session = Depends(get_db),
    current_user: User = Depends(user_access_required),
):
    """Clear all notifications for the current user."""
    user_id = current_user.id
    
    query = db.query(Notification).filter(
        (Notification.user_id == user_id) | (Notification.user_id.is_(None))
    )
    
    if read_only:
        query = query.filter(Notification.is_read.isnot(None))
    
    query.delete(synchronize_session=False)
    db.commit()
    
    return {"success": True}
