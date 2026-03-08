"""Toast Notification Demo Routes.

This module provides demo routes to showcase the toast notification system.
Remove or disable in production.
"""

from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.utils.template_helper import templates
from app.utils.notification_service import NotificationService
from app.routes.auth import admin_access_required
from app.models.user import User

router = APIRouter(prefix="/demo/toast", tags=["Demo"])


@router.get("/")
async def toast_demo_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
):
    """Render the toast demo page."""
    return templates.TemplateResponse("demo/toast_demo.html", {"request": request})


@router.post("/test-success")
async def test_success_notification():
    """Send a test success notification to all connected clients."""
    await NotificationService.success(
        message="This is a test success message!",
        title="Test Success",
        duration=5000,
    )
    return {"status": "success", "message": "Success notification sent"}


@router.post("/test-error")
async def test_error_notification():
    """Send a test error notification."""
    await NotificationService.error(
        message="Something went wrong! This is how errors look.",
        title="Test Error",
        duration=8000,
    )
    return {"status": "error", "message": "Error notification sent"}


@router.post("/test-warning")
async def test_warning_notification():
    """Send a test warning notification."""
    await NotificationService.warning(
        message="This is a warning. Please pay attention!",
        title="Test Warning",
        duration=6000,
    )
    return {"status": "warning", "message": "Warning notification sent"}


@router.post("/test-info")
async def test_info_notification():
    """Send a test info notification."""
    await NotificationService.info(
        message="Just some informational content for you.",
        title="Test Info",
        duration=5000,
    )
    return {"status": "info", "message": "Info notification sent"}


@router.post("/test-with-actions")
async def test_notification_with_actions():
    """Send a notification with action buttons."""
    await NotificationService.success(
        message="A new snapshot has been captured from Camera 01",
        title="Snapshot Saved",
        duration=10000,
        actions=[
            {"label": "View Snapshot", "url": "/snap_gallery"},
            {"label": "All Cameras", "url": "/cameras"},
        ],
    )
    return {"status": "success", "message": "Notification with actions sent"}


@router.post("/test-camera-offline")
async def test_camera_offline():
    """Simulate camera offline notification."""
    await NotificationService.camera_offline(
        camera_name="Front Gate Camera",
        camera_id=1,
    )
    return {"status": "success", "message": "Camera offline notification sent"}


@router.post("/test-camera-online")
async def test_camera_online():
    """Simulate camera online notification."""
    await NotificationService.camera_online(
        camera_name="Front Gate Camera",
        camera_id=1,
    )
    return {"status": "success", "message": "Camera online notification sent"}


@router.post("/test-storage-warning")
async def test_storage_warning():
    """Simulate storage warning notification."""
    await NotificationService.storage_warning(
        used_percent=87.5,
    )
    return {"status": "success", "message": "Storage warning notification sent"}


@router.post("/test-persistent")
async def test_persistent_notification():
    """Send a persistent notification (no auto-dismiss)."""
    await NotificationService.error(
        message="This is a critical message that requires user attention.",
        title="Critical Alert",
        duration=0,  # 0 = persistent
        dismissible=False,
    )
    return {"status": "success", "message": "Persistent notification sent"}


@router.post("/test-position/{position}")
async def test_position_notification(position: str):
    """Send a notification to a specific position."""
    from app.utils.notification_service import ToastPosition
    
    valid_positions = ["top-right", "top-left", "top-center", 
                      "bottom-right", "bottom-left", "bottom-center"]
    
    if position not in valid_positions:
        return {"error": f"Invalid position. Use one of: {', '.join(valid_positions)}"}
    
    await NotificationService.info(
        message=f"This toast appears at {position}",
        title="Position Test",
        position=position,
        duration=5000,
    )
    return {"status": "success", "message": f"Notification sent to {position}"}
