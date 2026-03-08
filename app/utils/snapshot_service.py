"""Snapshot Service with Toast Notifications.

This module provides snapshot operations with integrated toast notifications
for real-time user feedback.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.utils.notification_service import NotificationService

logger = logging.getLogger("snapshot_service")


def take_snapshot(camera: Camera, db: Session) -> Dict[str, Any]:
    """Take a snapshot from a camera.
    
    This is the legacy function used by scheduler and other parts of the app.
    
    Args:
        camera: The camera object to take snapshot from
        db: Database session
        
    Returns:
        Dict with keys:
        - status: "success" or "error"
        - file_path: Path to saved snapshot (if success)
        - resolution: Image resolution (if success)
        - message: Error message (if error)
    """
    import cv2
    import uuid
    from pathlib import Path
    
    try:
        # Build RTSP URL
        username = camera.username or ""
        password = camera.password or ""
        ip = camera.ip
        rtsp_path = camera.rtsp_url or ""
        
        if username and password:
            rtsp_url = f"rtsp://{username}:{password}@{ip}{rtsp_path}"
        else:
            rtsp_url = f"rtsp://{ip}{rtsp_path}"
        
        logger.info(f"Taking snapshot from {camera.name} at {ip}")
        
        # Open video capture
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not cap.isOpened():
            logger.error(f"Failed to connect to camera {camera.name}")
            return {
                "status": "error",
                "message": f"Cannot connect to camera {camera.name}"
            }
        
        # Read frame
        ret, frame = cap.read()
        cap.release()
        
        if not ret or frame is None:
            logger.error(f"Failed to capture frame from {camera.name}")
            return {
                "status": "error", 
                "message": f"Failed to capture frame from {camera.name}"
            }
        
        # Get resolution
        height, width = frame.shape[:2]
        resolution = f"{width}x{height}"
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_camera_name = "".join(c for c in camera.name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_camera_name = safe_camera_name.replace(' ', '_')
        filename = f"{safe_camera_name}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        
        # Ensure directory exists
        snapshot_dir = Path("static/snapshots")
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = snapshot_dir / filename
        
        # Save image
        cv2.imwrite(str(file_path), frame)
        
        # Return relative path
        relative_path = f"snapshots/{filename}"
        
        logger.info(f"Snapshot saved: {relative_path} ({resolution})")
        
        return {
            "status": "success",
            "file_path": relative_path,
            "resolution": resolution
        }
        
    except Exception as e:
        logger.error(f"Error taking snapshot from {camera.name}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


class SnapshotService:
    """Service for handling snapshot operations with notifications."""
    
    @staticmethod
    async def capture_snapshot(
        camera_id: int,
        db: Session,
        triggered_by: str = "manual",
    ) -> Optional[Snapshot]:
        """Capture snapshot from camera with notifications.
        
        Args:
            camera_id: ID of the camera to capture from
            db: Database session
            triggered_by: Who/what triggered the capture (manual, scheduled, etc.)
            
        Returns:
            The created Snapshot object or None if failed
        """
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            await NotificationService.error(
                message=f"Camera with ID {camera_id} not found",
                title="Snapshot Failed",
            )
            return None
        
        # Check if camera is online
        if not camera.is_active:
            await NotificationService.warning(
                message=f"Camera '{camera.name}' is currently disabled",
                title="Camera Disabled",
                camera_id=camera_id,
            )
            return None
        
        try:
            # Use the legacy take_snapshot function
            result = take_snapshot(camera, db)
            
            if result["status"] != "success":
                await NotificationService.error(
                    message=result.get("message", "Unknown error"),
                    title="Snapshot Failed",
                    camera_id=camera_id,
                )
                return None
            
            # Create snapshot record
            from app.utils.snapshot_utils import record_snapshot_metadata
            
            snapshot = record_snapshot_metadata(
                db=db,
                camera_id=camera_id,
                file_path=result["file_path"],
                resolution=result.get("resolution", "N/A"),
            )
            
            if not snapshot:
                await NotificationService.error(
                    message="Failed to record snapshot metadata",
                    title="Snapshot Error",
                    camera_id=camera_id,
                )
                return None
            
            # Notify success
            await NotificationService.snapshot_saved(
                camera_name=camera.name,
                camera_id=camera_id,
                snapshot_id=snapshot.id,
            )
            
            logger.info(f"Snapshot captured from {camera.name} (ID: {snapshot.id})")
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to capture snapshot from {camera.name}: {e}")
            
            await NotificationService.error(
                message=f"Failed to capture snapshot: {str(e)}",
                title="Snapshot Error",
                camera_id=camera_id,
            )
            return None
    
    @staticmethod
    async def bulk_capture(
        camera_ids: list[int],
        db: Session,
        triggered_by: str = "bulk",
    ) -> dict:
        """Capture snapshots from multiple cameras.
        
        Args:
            camera_ids: List of camera IDs
            db: Database session
            triggered_by: Who triggered the capture
            
        Returns:
            Dict with success count and failed cameras
        """
        results = {
            "success": [],
            "failed": [],
            "total": len(camera_ids),
        }
        
        await NotificationService.info(
            message=f"Starting bulk snapshot for {len(camera_ids)} cameras...",
            title="Bulk Snapshot Started",
            duration=3000,
        )
        
        for camera_id in camera_ids:
            snapshot = await SnapshotService.capture_snapshot(
                camera_id=camera_id,
                db=db,
                triggered_by=triggered_by,
            )
            if snapshot:
                results["success"].append(camera_id)
            else:
                results["failed"].append(camera_id)
        
        # Summary notification
        if results["failed"]:
            await NotificationService.warning(
                message=f"Bulk capture complete: {len(results['success'])} succeeded, {len(results['failed'])} failed",
                title="Bulk Snapshot Complete",
                duration=6000,
            )
        else:
            await NotificationService.success(
                message=f"Successfully captured snapshots from all {len(results['success'])} cameras",
                title="Bulk Snapshot Complete",
                duration=4000,
            )
        
        return results
    
    @staticmethod
    async def delete_snapshot(
        snapshot_id: str,
        db: Session,
        user_name: str = "system",
    ) -> bool:
        """Delete a snapshot with notification.
        
        Args:
            snapshot_id: ID of the snapshot to delete
            db: Database session
            user_name: Name of user performing the deletion
            
        Returns:
            True if deleted successfully
        """
        snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
        if not snapshot:
            await NotificationService.error(
                message=f"Snapshot with ID {snapshot_id} not found",
                title="Delete Failed",
            )
            return False
        
        try:
            # Delete file if exists
            if snapshot.file_path:
                full_path = os.path.join("static", snapshot.file_path)
                if os.path.exists(full_path):
                    os.remove(full_path)
            
            camera_name = snapshot.camera_name
            db.delete(snapshot)
            db.commit()
            
            await NotificationService.success(
                message=f"Snapshot from '{camera_name}' deleted by {user_name}",
                title="Snapshot Deleted",
                duration=3000,
            )
            
            logger.info(f"Snapshot {snapshot_id} deleted by {user_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            
            await NotificationService.error(
                message=f"Failed to delete snapshot: {str(e)}",
                title="Delete Error",
            )
            return False


# Convenience function for quick notifications
async def notify_snapshot_progress(current: int, total: int, camera_name: str):
    """Show progress notification for snapshot operations."""
    percent = int((current / total) * 100)
    await NotificationService.info(
        message=f"Capturing from {camera_name}... ({current}/{total}, {percent}%)",
        title="Snapshot Progress",
        duration=2000,
    )
