"""Snapshot Service with Toast Notifications.

This module provides snapshot operations with integrated toast notifications
for real-time user feedback.
"""

import logging
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urlunparse, quote

from sqlalchemy.orm import Session

from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.utils.notification_service import NotificationService
from app.utils.check_stats import check_stats
from app.utils.camera_capture import read_frame
from app.utils.wa_executor import run_camera_blocking
from app.utils.snapshot_locker import get_camera_lock

logger = logging.getLogger("snapshot_service")

def _capture_camera_sync(camera: SimpleNamespace) -> Dict[str, Any]:
    """Capture a camera without sharing a SQLAlchemy session with the thread."""
    try:
        with get_camera_lock(camera.id):
            return take_snapshot(camera, None)
    except RuntimeError:
        logger.info("Skipping concurrent snapshot capture for camera %s", camera.id)
        return {"status": "error", "message": "A snapshot capture is already running for this camera."}


def _mask_password_in_url(url: str) -> str:
    """Mask password in URL for logging purposes.
    
    Args:
        url: URL that may contain credentials
        
    Returns:
        URL with password masked as '***'
    """
    if not url or '@' not in url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            # Rebuild netloc with masked password
            netloc = parsed.hostname or ''
            if parsed.port:
                netloc += f":{parsed.port}"
            if parsed.username:
                auth = parsed.username
                if parsed.password:
                    auth += ":***"
                netloc = f"{auth}@{netloc}"
            parsed = parsed._replace(netloc=netloc)
            return urlunparse(parsed)
    except Exception:
        pass
    return url


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
        request_url = url
        capture_url = url

        # Add authentication for OpenCV only. Requests handles auth explicitly so it can
        # retry with Digest auth when camera endpoints reject Basic auth.
        if capture_url.startswith(('http://', 'https://')) and camera.username:
            parsed = list(urlparse(url))
            # Insert credentials into URL
            auth = f"{quote(camera.username)}:{quote(camera.password or '')}@"
            # Find where netloc starts and insert auth
            if '@' not in parsed[1]:  # Check if auth not already in URL
                parsed[1] = auth + parsed[1]
            capture_url = urlunparse(parsed)

        frame = None
        http_error = None

        # Direct HTTP snapshot endpoints (for example /oneshotimage) are usually a
        # single JPEG response, not a video stream. Try HTTP first so we can identify
        # auth/status/content-type failures clearly and support Digest auth.
        if request_url.startswith(('http://', 'https://')):
            import requests
            import numpy as np
            from requests.auth import HTTPBasicAuth, HTTPDigestAuth

            auth_attempts = [("none", None)]
            if camera.username:
                auth_attempts = [
                    ("basic", HTTPBasicAuth(camera.username, camera.password or "")),
                    ("digest", HTTPDigestAuth(camera.username, camera.password or "")),
                ]

            last_error = None
            for auth_name, auth in auth_attempts:
                try:
                    resp = requests.get(request_url, timeout=10, auth=auth)
                    content_type = resp.headers.get("content-type", "")

                    if resp.status_code in (401, 403):
                        last_error = (
                            f"HTTP {resp.status_code} with {auth_name} auth "
                            f"(content-type: {content_type or 'unknown'})"
                        )
                        logger.warning(
                            "Snapshot URL auth failed for %s via %s auth: HTTP %s",
                            camera.hostname,
                            auth_name,
                            resp.status_code,
                        )
                        continue

                    resp.raise_for_status()
                    img_array = np.frombuffer(resp.content, np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

                    if frame is not None:
                        logger.info(
                            "Snapshot URL fetched for %s via HTTP %s auth: HTTP %s, content-type=%s, bytes=%d",
                            camera.hostname,
                            auth_name,
                            resp.status_code,
                            content_type or "unknown",
                            len(resp.content),
                        )
                        break

                    last_error = (
                        f"HTTP {resp.status_code} returned undecodable image "
                        f"(content-type: {content_type or 'unknown'}, bytes: {len(resp.content)})"
                    )
                except Exception as e:
                    last_error = str(e)

            if frame is None and last_error:
                http_error = last_error
                logger.warning(
                    "Direct HTTP snapshot fetch failed for %s from %s: %s. Falling back to OpenCV.",
                    camera.hostname,
                    _mask_password_in_url(request_url),
                    last_error,
                )
        
        if frame is None:
            # Try to open as video capture (works for RTSP/HTTP MJPEG streams and some direct image URLs)
            frame = read_frame(capture_url)
            if frame is None:
                logger.error("Failed to open snapshot URL with OpenCV: %s", _mask_password_in_url(capture_url))
                return {
                    "status": "error",
                    "message": f"Cannot access snapshot URL for {camera.hostname}: {http_error or 'OpenCV connection failed'}"
                }

        
        # Get resolution
        height, width = frame.shape[:2]
        resolution = f"{width}x{height}"
        
        # Generate filename with folder structure: snapshots/<camera_id>/<date>/<filename>
        # Using UTC for consistency with database timestamps
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        timestamp = datetime.now(timezone.utc).strftime("%H%M%S")
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
            logger.info(f"Taking snapshot from {camera.hostname} using direct URL: {_mask_password_in_url(camera.snapshot_url)}")
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
        
        logger.info(f"Taking snapshot from {camera.hostname} at {_mask_password_in_url(rtsp_url)}")
        
        # Open video capture
        frame = read_frame(rtsp_url)
        if frame is None:
            logger.error(f"Failed to connect to camera {camera.hostname}")
            return {
                "status": "error",
                "message": f"Cannot connect to camera {camera.hostname}"
            }
        
        
        # Get resolution
        height, width = frame.shape[:2]
        resolution = f"{width}x{height}"
        
        # Generate filename with folder structure: snapshots/<camera_id>/<date>/<filename>
        # Using UTC for consistency with database timestamps
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        timestamp = datetime.now(timezone.utc).strftime("%H-%M-%S")
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
            # The capture path performs blocking OpenCV, RTSP/ONVIF, HTTP and
            # image operations. Pass only loaded scalar settings to the thread.
            camera_config = SimpleNamespace(
                id=str(camera.id),
                hostname=camera.hostname,
                snapshot_url=camera.snapshot_url,
                username=camera.username,
                password=camera.password,
                ip=camera.ip,
                port=camera.port,
            )
            result = await run_camera_blocking(_capture_camera_sync, camera_config)
            
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
            
            # Update camera daily stats
            check_stats(camera)
            
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
        hard_delete: bool = False,  # P0-002: Soft delete by default
    ) -> bool:
        """Delete a snapshot with notification.
        
        Args:
            snapshot_id: ID of the snapshot to delete
            db: Database session
            user_name: Name of user performing the deletion
            hard_delete: If True, permanently delete (admin only). Default is soft delete.
            
        Returns:
            True if deleted successfully
        """
        from datetime import datetime, timezone
        
        snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
        if not snapshot:
            # Note: Error notification is handled by frontend
            return False
        
        try:
            if hard_delete:
                # P0-002: Hard delete - permanently remove file and record (admin only)
                if snapshot.file_path:
                    full_path = os.path.join("static", "snapshots", snapshot.file_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)
                
                db.delete(snapshot)
                db.commit()
                logger.info(f"Snapshot {snapshot_id} HARD deleted by {user_name}")
            else:
                # P0-002: Soft delete - mark as deleted but preserve data
                snapshot.soft_delete()
                db.commit()
                logger.info(f"Snapshot {snapshot_id} SOFT deleted by {user_name}")
            
            # Note: Success notification is handled by frontend
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            # Note: Error notification is handled by frontend
            return False
    
    @staticmethod
    async def restore_snapshot(
        snapshot_id: str,
        db: Session,
        user_name: str = "system",
    ) -> bool:
        """Restore a soft-deleted snapshot.
        
        Args:
            snapshot_id: ID of the snapshot to restore
            db: Database session
            user_name: Name of user performing the restoration
            
        Returns:
            True if restored successfully
        """
        snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
        if not snapshot:
            return False
        
        if not snapshot.is_deleted:
            logger.warning(f"Snapshot {snapshot_id} is not deleted, nothing to restore")
            return False
        
        try:
            snapshot.restore()
            db.commit()
            logger.info(f"Snapshot {snapshot_id} restored by {user_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore snapshot {snapshot_id}: {e}")
            return False
    
    @staticmethod
    def purge_deleted_snapshots(
        db: Session,
        days_old: int = 30,
        user_name: str = "system",
        skip_retention_hold: bool = True,
    ) -> int:
        """Permanently delete snapshots that have been soft-deleted for specified days.
        
        Args:
            db: Database session
            days_old: Delete snapshots soft-deleted more than this many days ago
            user_name: Name of user performing the purge
            skip_retention_hold: If True, skip snapshots with retention_hold=True
            
        Returns:
            Number of snapshots purged
        """
        from datetime import datetime, timezone, timedelta
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        
        # Build query
        query = db.query(Snapshot).filter(
            Snapshot.deleted_at.isnot(None),
            Snapshot.deleted_at < cutoff_date
        )
        
        # P2-001: Skip retention hold items if requested
        if skip_retention_hold:
            query = query.filter(Snapshot.retention_hold == False)
        
        snapshots_to_purge = query.all()
        
        purged_count = 0
        for snapshot in snapshots_to_purge:
            try:
                # Delete file
                if snapshot.file_path:
                    full_path = os.path.join("static", "snapshots", snapshot.file_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)
                
                db.delete(snapshot)
                purged_count += 1
            except Exception as e:
                logger.error(f"Failed to purge snapshot {snapshot.id}: {e}")
        
        if purged_count > 0:
            db.commit()
            logger.info(f"Purged {purged_count} soft-deleted snapshots older than {days_old} days by {user_name}")
        
        return purged_count


# Convenience function for quick notifications
async def notify_snapshot_progress(current: int, total: int, camera_name: str):
    """Show progress notification for snapshot operations."""
    percent = int((current / total) * 100)
    await NotificationService.info(
        message=f"Capturing from {camera_name}... ({current}/{total}, {percent}%)",
        title="Snapshot Progress",
        duration=2000,
    )
