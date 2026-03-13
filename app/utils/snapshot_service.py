"""Snapshot Service with Toast Notifications.

This module provides snapshot operations with integrated toast notifications
for real-time user feedback.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urlunparse, quote

from sqlalchemy.orm import Session

from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.utils.notification_service import NotificationService

logger = logging.getLogger("snapshot_service")


def _take_snapshot_from_url(camera: Camera, url: str) -> Dict[str, Any]:
    """Take snapshot from a direct HTTP URL (e.g., http://camera_ip/cgi-bin/snapshot.cgi).
    
    Args:
        camera: The camera object
        url: Direct snapshot URL
        
    Returns:
        Dict with status, file_path, resolution (if success) or message (if error)
    """
    import cv2
    import uuid
    from pathlib import Path
    from urllib.parse import quote
    
    try:
        # Add authentication if username/password provided and URL is HTTP/HTTPS
        if url.startswith(('http://', 'https://')) and camera.username:
            parsed = list(urlparse(url))
            # Insert credentials into URL
            auth = f"{quote(camera.username)}:{quote(camera.password or '')}@"
            # Find where netloc starts and insert auth
            if '@' not in parsed[1]:  # Check if auth not already in URL
                parsed[1] = auth + parsed[1]
            url = urlunparse(parsed)
        
        # Try to open as video capture (works for both HTTP MJPEG streams and direct image URLs)
        cap = cv2.VideoCapture(url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not cap.isOpened():
            # If direct capture fails, try downloading as image
            import requests
            from io import BytesIO
            import numpy as np
            
            try:
                resp = requests.get(url, timeout=10, auth=(camera.username, camera.password) if camera.username else None)
                resp.raise_for_status()
                img_array = np.frombuffer(resp.content, np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                
                if frame is None:
                    raise ValueError("Failed to decode image from URL")
                    
                cap.release()  # Release the failed capture
            except Exception as e:
                logger.error(f"Failed to download snapshot from URL {url}: {e}")
                return {
                    "status": "error",
                    "message": f"Cannot access snapshot URL for {camera.hostname}: {str(e)}"
                }
        else:
            # Read frame from video capture
            ret, frame = cap.read()
            cap.release()
            
            if not ret or frame is None:
                return {
                    "status": "error",
                    "message": f"Failed to capture frame from {camera.hostname}"
                }
        
        # Get resolution
        height, width = frame.shape[:2]
        resolution = f"{width}x{height}"
        
        # Generate filename with folder structure: snapshots/<camera_id>/<date>/<filename>
        today = datetime.now().strftime("%Y%m%d")
        timestamp = datetime.now().strftime("%H%M%S")
        safe_camera_name = "".join(c for c in camera.hostname if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_camera_name = safe_camera_name.replace(' ', '_')
        filename = f"{safe_camera_name}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        
        # Create folder structure: snapshots/<camera_id>/<date>/
        snapshot_dir = Path("static/snapshots") / camera.id / today
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = snapshot_dir / filename
        
        # Save image
        cv2.imwrite(str(file_path), frame)
        
        # Add watermark
        try:
            from app.utils.image_utils import add_watermark
            from app.core.config import get_config_value
            watermark_text = get_config_value("watermark_text", "Property of B-SNAP")
            add_watermark(str(file_path), watermark_text)
        except Exception as e:
            logger.warning(f"Failed to add watermark: {e}")
        
        # Return relative path including folder structure (without 'snapshots/' prefix since SNAPSHOT_BASE_DIR already includes it)
        relative_path = f"{camera.id}/{today}/{filename}"
        
        logger.info(f"Snapshot saved from direct URL: snapshots/{relative_path} ({resolution})")
        
        return {
            "status": "success",
            "file_path": relative_path,
            "resolution": resolution
        }
        
    except Exception as e:
        logger.error(f"Error taking snapshot from URL for {camera.hostname}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


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
        # Check if camera has direct snapshot URL first
        if camera.snapshot_url:
            logger.info(f"Taking snapshot from {camera.hostname} using direct URL: {camera.snapshot_url}")
            return _take_snapshot_from_url(camera, camera.snapshot_url)
        
        # Otherwise, use RTSP via ONVIF helper
        from app.utils.camera_onvif import get_rtsp_url
        rtsp_url = get_rtsp_url(camera)
        
        if not rtsp_url:
            logger.error(f"Failed to get RTSP URL for camera {camera.hostname}")
            return {
                "status": "error",
                "message": f"Cannot determine RTSP URL for camera {camera.hostname}. Check camera settings."
            }
        
        logger.info(f"Taking snapshot from {camera.hostname} at {rtsp_url}")
        
        # Open video capture
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not cap.isOpened():
            logger.error(f"Failed to connect to camera {camera.hostname}")
            return {
                "status": "error",
                "message": f"Cannot connect to camera {camera.hostname}"
            }
        
        # Read frame
        ret, frame = cap.read()
        cap.release()
        
        if not ret or frame is None:
            logger.error(f"Failed to capture frame from {camera.hostname}")
            return {
                "status": "error", 
                "message": f"Failed to capture frame from {camera.hostname}"
            }
        
        # Get resolution
        height, width = frame.shape[:2]
        resolution = f"{width}x{height}"
        
        # Generate filename with folder structure: snapshots/<camera_id>/<date>/<filename>
        today = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%H-%M-%S")
        safe_camera_name = "".join(c for c in camera.hostname if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_camera_name = safe_camera_name.replace(' ', '_')
        filename = f"{safe_camera_name}_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        
        # Create folder structure: snapshots/<camera_id>/<date>/
        snapshot_dir = Path("static/snapshots") / camera.id / today
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = snapshot_dir / filename
        
        # Save image
        cv2.imwrite(str(file_path), frame)
        
        # Add watermark
        try:
            from app.utils.image_utils import add_watermark
            from app.core.config import get_config
            watermark_text = get_config("watermark_text", "Property of B-SNAP")
            add_watermark(str(file_path), watermark_text)
        except Exception as e:
            logger.warning(f"Failed to add watermark: {e}")
        
        # Return relative path including folder structure (without 'snapshots/' prefix since SNAPSHOT_BASE_DIR already includes it)
        relative_path = f"{camera.id}/{today}/{filename}"
        
        logger.info(f"Snapshot saved: snapshots/{relative_path} ({resolution})")
        
        return {
            "status": "success",
            "file_path": relative_path,
            "resolution": resolution
        }
        
    except Exception as e:
        logger.error(f"Error taking snapshot from {camera.hostname}: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


class SnapshotService:
    """Service for handling snapshot operations with notifications."""
    
    @staticmethod
    async def capture_snapshot(
        camera_id: str,
        db: Session,
        triggered_by: str = "manual",
    ) -> Optional[Snapshot]:
        """Capture snapshot from camera with notifications.
        
        Args:
            camera_id: ID of the camera to capture from (UUID string)
            db: Database session
            triggered_by: Who/what triggered the capture (manual, scheduled, etc.)
            
        Returns:
            The created Snapshot object or None if failed
        """
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            # Note: Error notification is handled by frontend
            return None
        
        # Check if camera is active (status should be Active, Restricted, or Maintenance)
        if camera.status not in ["Active", "Restricted", "Maintenance"]:
            # Note: Warning notification is handled by frontend
            return None
        
        try:
            # Use the legacy take_snapshot function
            result = take_snapshot(camera, db)
            
            if result["status"] != "success":
                # Note: Error notification is handled by frontend
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
                # Note: Error notification is handled by frontend
                return None
            
            # Note: Success notification is handled by frontend
            
            logger.info(f"Snapshot captured from {camera.hostname} (ID: {snapshot.id})")
            return snapshot
            
        except Exception as e:
            logger.error(f"Failed to capture snapshot from {camera.hostname}: {e}")
            # Note: Error notification is handled by frontend
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
        
        # Note: Notifications are handled by caller (frontend or scheduler)
        
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
            # Note: Error notification is handled by frontend
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
            
            # Note: Success notification is handled by frontend
            
            logger.info(f"Snapshot {snapshot_id} deleted by {user_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            # Note: Error notification is handled by frontend
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
