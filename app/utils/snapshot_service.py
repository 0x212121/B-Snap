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


def maybe_flip_image(image_path: str, is_flipped: bool):
    """
    Flip image horizontally if is_flipped is True.
    """
    if not is_flipped:
        return

    try:
        img = cv2.imread(image_path)
        if img is None:
            raise RuntimeError(f"Failed to load image for flipping: {image_path}")
        flipped = cv2.flip(img, -1)
        cv2.imwrite(image_path, flipped)
        logger.info("Flipped image: %s", image_path)
    except Exception as e:
        logger.error("Failed to flip image %s: %s", image_path, e)


def take_snapshot(camera: Camera, db: Session) -> dict:
    if not is_reachable(camera.ip):
        msg = "\u26a0\ufe0f [%s] unreachable (ping failed)" % camera.hostname
        logger.warning(msg)
        return error_response(camera.hostname, "Camera offline")

    result = try_http_snapshot(camera, db)
    if result["status"] == "success":
        return result

    logger.warning("Falling back to RTSP for %s", camera.hostname)
    result = try_rtsp_snapshot(camera, db)
    if result["status"] == "success":
        return result

    logger.warning("Falling back to FFmpeg for %s", camera.hostname)
    return try_ffmpeg_snapshot(camera, db)


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
            raise RuntimeError("HTTP %d" % response.status_code)

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
        maybe_flip_image(full_path, is_flipped=camera.is_flipped)
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
            "\u26a0\ufe0f HTTP snapshot failed for %s. Falling back to RTSP.",
            camera.hostname,
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
        maybe_flip_image(full_path, is_flipped=camera.is_flipped)
        add_watermark(full_path, text=WATERMARK_TEXT, opacity=0.5)
        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)

        logger.info("\u2705 [%s] RTSP snapshot \u2192 %s", camera.hostname, relative_path)
        return {
            "status": "success",
            "message": f"Snapshot successfully taken from {camera.hostname} (via RTSP)",
            "camera_name": camera.hostname,
            "file_path": relative_path,
            "resolution": resolution,
            "camera_ip": camera.ip
        }

    except Exception as e:
        logger.exception("\u274c [%s] RTSP snapshot failed definitively.", camera.hostname)
        return error_response(camera.hostname, f"RTSP snapshot failed: {str(e)}")

    finally:
        if cap and cap.isOpened():  # Check if it was opened before releasing
            cap.release()


def try_ffmpeg_snapshot(camera: Camera, db: Session) -> dict:
    WATERMARK_TEXT = get_config("watermark_text", default="Property of Company")
    try:
        rtsp_url = get_rtsp_url(camera)
        if not rtsp_url:
            raise RuntimeError("RTSP URL not available")

        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        dir_path = os.path.join(STATIC_DIR, str(camera.id), date_str)
        os.makedirs(dir_path, exist_ok=True)

        filename = f"{time_str}.jpg"
        full_path = os.path.join(dir_path, filename)

        # 🧠 Gunakan ffmpeg CLI langsung
        cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-y",  # overwrite if exists
            "-i", rtsp_url,
            "-frames:v", "1",
            "-q:v", "2",
            "-timeout", "5000000",
            full_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

        if result.returncode != 0 or not os.path.exists(full_path):
            raise RuntimeError(f"FFmpeg failed with return code {result.returncode}")

        with open(full_path, "rb") as f:
            image_bytes = f.read()

        resolution = get_image_resolution(image_bytes)
        relative_path = os.path.relpath(full_path, start=STATIC_DIR).replace("\\", "/")

        maybe_flip_image(full_path, is_flipped=camera.is_flipped)
        add_watermark(full_path, text=WATERMARK_TEXT, opacity=0.5)
        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)

        logger.info("📸 [%s] FFmpeg snapshot → %s", camera.hostname, relative_path)

        return {
            "status": "success",
            "message": f"Snapshot taken from {camera.hostname} (via ffmpeg)",
            "camera_name": camera.hostname,
            "file_path": relative_path,
            "resolution": resolution,
            "camera_ip": camera.ip
        }

    except Exception as e:
        logger.exception("❌ [%s] FFmpeg snapshot failed.", camera.hostname)
        return error_response(camera.hostname, f"FFmpeg snapshot failed: {str(e)}")


def error_response(camera_name: str, error: str) -> dict:
    return {
        "status": "error",
        "message": f"Failed to take snapshot from {camera_name}: {error}",
        "camera_name": camera_name
    }


def clean_old_snapshots(camera_name: str, db: Session):
    max_screenshots = int(get_config("max_screenshot_per_camera", default=3))
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
                logger.info("Deleted old snapshot file: %s", snap.file_path)
            else:
                logger.warning("Old snapshot file not found, skipping: %s", path)
        except Exception as e:
            logger.error("Failed to delete %s: %s", snap.file_path, e)
        
        try:
            db.delete(snap)
        except Exception as e:
            logger.error("Failed to delete snapshot DB entry: %s", e)
    try:
        db.commit()
        logger.info("Deleted %d old snapshots from DB", len(to_delete))
    except Exception as e:
        logger.error("Failed to commit snapshot deletion: %s", e)
