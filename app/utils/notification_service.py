"""Toast Notification Service for B-Snap.

This module sends live toast notifications through authenticated WebSockets.
The persistent option controls on-screen dismissal, not database storage.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from app.ws.manager import websocket_connections
from app.ws.record_notifications import deliver_notification

logger = logging.getLogger("notifications")


class ToastType(str, Enum):
    """Types of toast notifications."""
    
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ToastPosition(str, Enum):
    """Positions for toast notifications."""
    
    TOP_RIGHT = "top-right"
    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    BOTTOM_RIGHT = "bottom-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"


class ToastNotification:
    """Toast notification data class."""

    def __init__(
        self,
        message: str,
        title: Optional[str] = None,
        type: ToastType = ToastType.INFO,
        duration: int = 5000,
        position: ToastPosition = ToastPosition.TOP_RIGHT,
        dismissible: bool = True,
        persistent: bool = False,
        user_id: Optional[int] = None,
        camera_id: Optional[str] = None,
        actions: Optional[list[dict[str, str]]] = None,
    ):
        self.message = message
        self.title = title
        self.type = type
        self.duration = duration
        self.position = position
        self.dismissible = dismissible
        self.persistent = persistent
        self.user_id = user_id
        self.camera_id = camera_id
        self.actions = actions or []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": "toast",
            "data": {
                "message": self.message,
                "title": self.title,
                "toast_type": self.type.value,
                "duration": self.duration,
                "position": self.position.value,
                "dismissible": self.dismissible,
                "persistent": self.persistent,
                "user_id": self.user_id,
                "camera_id": self.camera_id,
                "actions": self.actions,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class NotificationService:
    """Service for sending notifications to users."""

    @staticmethod
    async def send_toast(
        message: str,
        title: Optional[str] = None,
        toast_type: ToastType = ToastType.INFO,
        duration: int = 5000,
        position: ToastPosition = ToastPosition.TOP_RIGHT,
        dismissible: bool = True,
        user_id: Optional[int] = None,
        camera_id: Optional[str] = None,
        actions: Optional[list[dict[str, str]]] = None,
    ) -> ToastNotification:
        """Send a toast notification via WebSocket.

        Args:
            message: The notification message
            title: Optional title for the notification
            toast_type: Type of notification (success, error, warning, info)
            duration: Duration in milliseconds (0 disables automatic dismissal)
            position: Position on screen
            dismissible: Whether user can dismiss the toast
            user_id: Target user ID (None = broadcast to all)
            camera_id: Associated camera ID
            actions: Optional action buttons [{"label": "View", "url": "/cameras/1"}]

        Returns:
            The created ToastNotification object
        """
        toast = ToastNotification(
            message=message,
            title=title,
            type=toast_type,
            duration=duration,
            position=position,
            dismissible=dismissible,
            user_id=user_id,
            camera_id=camera_id,
            actions=actions,
        )

        # Send via WebSocket
        await NotificationService._broadcast(toast)

        logger.info(f"Toast sent: [{toast_type.value}] {message[:50]}...")
        return toast

    @staticmethod
    async def _broadcast(toast: ToastNotification) -> None:
        """Broadcast toast to WebSocket connections."""
        if not websocket_connections:
            logger.debug("No WebSocket connections available")
            return

        disconnected = set()
        message = toast.to_json()

        for ws in list(websocket_connections):
            try:
                await deliver_notification(ws, toast.to_dict(), message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.add(ws)

        # Clean up disconnected websockets
        for ws in disconnected:
            websocket_connections.discard(ws)

    # ==================== Convenience Methods ====================

    @staticmethod
    async def success(
        message: str,
        title: str = "Success",
        duration: int = 5000,
        **kwargs
    ) -> ToastNotification:
        """Send a success toast."""
        return await NotificationService.send_toast(
            message=message,
            title=title,
            toast_type=ToastType.SUCCESS,
            duration=duration,
            **kwargs
        )

    @staticmethod
    async def error(
        message: str,
        title: str = "Error",
        duration: int = 8000,
        **kwargs
    ) -> ToastNotification:
        """Send an error toast."""
        return await NotificationService.send_toast(
            message=message,
            title=title,
            toast_type=ToastType.ERROR,
            duration=duration,
            **kwargs
        )

    @staticmethod
    async def warning(
        message: str,
        title: str = "Warning",
        duration: int = 6000,
        **kwargs
    ) -> ToastNotification:
        """Send a warning toast."""
        return await NotificationService.send_toast(
            message=message,
            title=title,
            toast_type=ToastType.WARNING,
            duration=duration,
            **kwargs
        )

    @staticmethod
    async def info(
        message: str,
        title: str = "Info",
        duration: int = 5000,
        **kwargs
    ) -> ToastNotification:
        """Send an info toast."""
        return await NotificationService.send_toast(
            message=message,
            title=title,
            toast_type=ToastType.INFO,
            duration=duration,
            **kwargs
        )

    # ==================== System Notifications ====================

    @staticmethod
    async def camera_offline(camera_name: str, camera_id: str, **kwargs) -> ToastNotification:
        """Notify when a camera goes offline."""
        return await NotificationService.error(
            message=f"Camera '{camera_name}' is offline",
            title="Camera Offline",
            camera_id=camera_id,
            actions=[{"label": "Check Status", "url": f"/health"}],
            **kwargs
        )

    @staticmethod
    async def camera_online(camera_name: str, camera_id: str, **kwargs) -> ToastNotification:
        """Notify when a camera comes online."""
        return await NotificationService.success(
            message=f"Camera '{camera_name}' is back online",
            title="Camera Online",
            camera_id=camera_id,
            duration=4000,
            **kwargs
        )

    @staticmethod
    async def snapshot_saved(
        camera_name: str, camera_id: str, snapshot_id: str, **kwargs
    ) -> ToastNotification:
        """Notify when snapshot is saved."""
        return await NotificationService.success(
            message=f"Snapshot saved from '{camera_name}'",
            title="Snapshot Captured",
            camera_id=camera_id,
            actions=[{"label": "View", "url": f"/snapshots/{snapshot_id}"}],
            duration=4000,
            **kwargs
        )


# ==================== HTTP Context Helper ====================

def get_notification_service() -> NotificationService:
    """Get notification service instance."""
    return NotificationService()


# ==================== Legacy Compatibility ====================

# Alias for backward compatibility
notify = NotificationService.send_toast
notify_success = NotificationService.success
notify_error = NotificationService.error
notify_warning = NotificationService.warning
notify_info = NotificationService.info
