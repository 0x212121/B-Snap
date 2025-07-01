from datetime import datetime
import io
import os
import time
import cv2
from io import BytesIO
from PIL import Image
import logging
from sqlalchemy.orm import Session
from onvif import ONVIFCamera

from app.core.config import get_config
from app.core.logging_config import setup_logging
from app.onvif_client import (
    is_reachable,
    add_watermark,
    get_rtsp_url,
    try_auth
)
from app.utils import check_stats
from app.models_sql import Camera, Snapshot

# BASE_DIR = os.path.dirname(os.path.dirname(__file__))
STATIC_DIR = os.path.join("static", "snapshots")

setup_logging()
logger = logging.getLogger("snapshot")


def get_image_resolution(image_bytes: bytes) -> str:
    with Image.open(BytesIO(image_bytes)) as img:
        width, height = img.size
    return f"{width}x{height}"


def save_snapshot_file(camera_id: str, image_bytes: bytes) -> tuple[str, str]:
    """
    Saves the snapshot file and returns a universal relative path (with forward slashes).
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H-%M-%S")
    dir_path = os.path.join(STATIC_DIR, camera_id, date_str)
    os.makedirs(dir_path, exist_ok=True)
    
    file_name = f"{time_str}.jpg"
    full_path = os.path.join(dir_path, file_name)
    
    with open(full_path, "wb") as f:
        f.write(image_bytes)
        
    resolution = get_image_resolution(image_bytes)
    relative_path = os.path.relpath(full_path, start=STATIC_DIR)

    # --- FIX APPLIED HERE ---
    # Ensure the path uses forward slashes for universal compatibility (web URLs, other OS)
    # This prevents issues with backslashes being interpreted as escape characters.
    universal_relative_path = relative_path.replace("\\", "/")
    
    return universal_relative_path, resolution


def take_snapshot(camera: Camera, db: Session) -> dict:
    if not is_reachable(camera.ip):
        msg = f"⚠️ [{camera.hostname}] unreachable (ping failed)"
        logger.warning(msg)
        return error_response(camera.hostname, "Camera offline")

    result = try_http_snapshot(camera, db)
    if result["status"] == "success":
        return result

    logger.warning(f"Falling back to RTSP for {camera.hostname}")
    return try_rtsp_snapshot(camera, db)


def try_http_snapshot(camera: Camera, db: Session) -> dict:
    WATERMARK_TEXT = get_config("watermark_text", default="Property of Company")
    try:
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        media = cam.create_media_service()
        profile = media.GetProfiles()[0]
        uri = media.GetSnapshotUri({"ProfileToken": profile.token}).Uri

        time.sleep(1)
        response = try_auth(uri, camera.username, camera.password)

        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")

        image_bytes = response.content

        time.sleep(0.5)

        # --- VALIDATE THE RECEIVED IMAGE ---
        if not image_bytes:
            raise RuntimeError("HTTP response was empty, no image data received.")
            
        try:
            # Use Pillow to check if the data is a valid and complete image.
            # We open the image from an in-memory byte stream.
            image_stream = io.BytesIO(image_bytes)
            with Image.open(image_stream) as img:
                # .verify() checks for file corruption and truncation.
                # It will raise an exception if the image is invalid.
                img.verify() 
        except Exception as e:
            # The image data is corrupt or incomplete.
            # Raise a new error to signal that this attempt failed,
            # which will trigger the RTSP fallback.
            raise RuntimeError(f"Invalid image data received via HTTP: {str(e)}")

        # If we get here, the image is valid. Now we can save it.
        relative_path, resolution = save_snapshot_file(str(camera.id), image_bytes)
        full_path = os.path.join(STATIC_DIR, *relative_path.split('/'))
        add_watermark(full_path, text=WATERMARK_TEXT, opacity=0.5)
        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)

        return {
            "status": "success",
            "message": f"Snapshot taken from {camera.hostname}",
            "camera_name": camera.hostname,
            "file_path": relative_path,
            "resolution": resolution,
            "camera_ip": camera.ip
        }

    except Exception as e:
        # This block now catches both HTTP errors and our new image validation errors.
        logger.warning(
            f"⚠️ HTTP snapshot failed for {camera.hostname}. Falling back to RTSP.",
            exc_info=True
        )
        return error_response(camera.hostname, str(e))


def try_rtsp_snapshot(camera: Camera, db: Session) -> dict:
    cap = None
    WATERMARK_TEXT = get_config("watermark_text", default="Property of Company")
    try:
        rtsp_uri = get_rtsp_url(camera)
        if not rtsp_uri:
            raise RuntimeError("RTSP URL not available")

        cap = cv2.VideoCapture()
        cap.open(rtsp_uri, apiPreference=cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError("Cannot open RTSP stream")
        
        for _ in range(5):  # Read and discard 5 frames
            cap.read()
        
        ret, frame = cap.read()
        if not ret or frame is None: # Added check for None frame
            raise RuntimeError("No valid frame received from RTSP after flushing buffer")

        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            raise RuntimeError("Failed to encode frame to JPEG")

        image_bytes = buffer.tobytes()
        relative_path, resolution = save_snapshot_file(str(camera.id), image_bytes)
        full_path = os.path.join(STATIC_DIR, *relative_path.split('/'))
        add_watermark(full_path, text=WATERMARK_TEXT, opacity=0.5)
        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)

        logger.info(f"✅ [{camera.hostname}] RTSP snapshot → {relative_path}")
        return {
            "status": "success",
            "message": f"Snapshot successfully taken from {camera.hostname} (via RTSP)",
            "camera_name": camera.hostname,
            "file_path": relative_path,
            "resolution": resolution,
            "camera_ip": camera.ip
        }

    except Exception as e:
        logger.exception(f"❌ [{camera.hostname}] RTSP snapshot failed definitively.")
        return error_response(camera.hostname, f"RTSP snapshot failed: {str(e)}")

    finally:
        if cap and cap.isOpened():  # Check if it was opened before releasing
            cap.release()


def error_response(camera_name: str, error: str) -> dict:
    return {
        "status": "error",
        "message": f"Failed to take snapshot from {camera_name}: {error}",
        "camera_name": camera_name
    }


def clean_old_snapshots(camera_name: str, db: Session):
    max_screenshots = int(get_config("max_screenshot_per_camera"))
    if max_screenshots <= 0:
        return
        
    snapshots = db.query(Snapshot).filter(
        Snapshot.camera_name == camera_name
    ).order_by(Snapshot.timestamp.desc()).all()

    if len(snapshots) < max_screenshots:
        return

    to_delete = snapshots[max_screenshots-1:]

    for snap in to_delete:
        # Reconstruct path correctly regardless of stored slash type
        path = os.path.join(STATIC_DIR, *snap.file_path.replace("\\", "/").split('/'))
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"Deleted old snapshot file: {snap.file_path}")
            else:
                logger.warning(f"Old snapshot file not found, skipping: {path}")
        except Exception as e:
            logger.error(f"Failed to delete {snap.file_path}: {e}")
        
        try:
            db.delete(snap)
        except Exception as e:
            logger.error(f"Failed to delete snapshot DB entry: {e}")

    try:
        db.commit()
        logger.info(f"Deleted {len(to_delete)} old snapshots from DB")
    except Exception as e:
        logger.error(f"Failed to commit snapshot deletion: {e}")
