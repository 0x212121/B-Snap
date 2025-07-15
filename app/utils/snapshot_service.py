import logging
import os
import time
import traceback
from datetime import datetime
from urllib.parse import quote, urlparse, urlunparse

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from onvif import ONVIFCamera
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from sqlalchemy.orm import Session

# --- Assumed Project Imports ---
# These imports are based on your provided script.
from app.core.config import get_config
from app.core.logging_config import setup_logging
from app.models_sql import Camera, Snapshot
from app.onvif_client import is_reachable, try_auth  # Assuming these helpers exist
from app.utils import check_stats

# --- Setup & Configuration ---
setup_logging()
logger = logging.getLogger("snapshot")

STATIC_DIR = os.path.join("static", "snapshots")
# A common font path on Linux. Change if your system is different.
FONT_PATH = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

# --- Core Snapshot & Watermarking Logic ---

def add_watermark_and_save_webp(frame: np.ndarray, output_path: str, text: str, opacity=0.5, color=(255, 255, 255), outline_color=(0, 0, 0), outline_width=2):
    """
    Adds a centered, word-wrapped watermark to an image frame and saves it as a WEBP file.
    """
    try:
        # Convert OpenCV's BGR frame to an RGBA PIL Image
        base_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        
        # Create a transparent layer for the text
        txt_layer = Image.new("RGBA", base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt_layer)

        # Font selection with fallback
        try:
            # Adjust font size based on image height for better scaling
            font_size = int(base_image.height * 0.05)
            font = ImageFont.truetype(FONT_PATH, font_size)
        except IOError:
            logger.warning(f"Font at {FONT_PATH} not found. Falling back to default font.")
            font_size = int(base_image.height * 0.05)
            font = ImageFont.load_default(size=font_size)

        # --- Text Wrapping Logic ---
        image_width, image_height = base_image.size
        max_pixel_width = int(image_width * 0.70) # Max width is 70% of image width

        def wrap_text(text_to_wrap, font, max_width):
            """Wraps text to fit within a specified pixel width."""
            lines = []
            # Handle multi-line input text correctly
            for line in text_to_wrap.split('\n'):
                words = line.split()
                if not words:
                    lines.append('')
                    continue
                
                current_line = words[0]
                for word in words[1:]:
                    # Check if adding the next word exceeds the max width
                    if font.getlength(current_line + ' ' + word) <= max_width:
                        current_line += ' ' + word
                    else:
                        lines.append(current_line)
                        current_line = word
                lines.append(current_line)
            return '\n'.join(lines)

        wrapped_text = wrap_text(text, font, max_pixel_width)

        # --- Centering and Drawing Logic ---
        center_x = image_width / 2
        center_y = image_height / 2
        
        fill_main = color + (int(255 * opacity),)
        fill_outline = outline_color + (int(255 * opacity),)

        # Draw outline by offsetting the text
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx != 0 or dy != 0:
                    draw.text((center_x + dx, center_y + dy), wrapped_text, font=font, fill=fill_outline, anchor="mm", align="center")

        # Draw the main text on top. anchor="mm" centers the text block at the given coordinates.
        draw.text((center_x, center_y), wrapped_text, font=font, fill=fill_main, anchor="mm", align="center")

        # Composite the text layer onto the base image
        watermarked = Image.alpha_composite(base_image, txt_layer)

        # Save as WEBP, quality 85 is a good balance
        watermarked.save(output_path, "WEBP", quality=85)
        logger.info(f"Watermarked snapshot saved to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to watermark and save frame: {e}\n{traceback.format_exc()}")
        return False


def generate_snapshot_path(camera_id: str) -> tuple[str, str] | tuple[None, None]:
    """
    Generates the full and relative paths for a new snapshot file.
    Ensures the directory exists and returns paths with a .webp extension.
    """
    try:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        
        dir_path = os.path.join(STATIC_DIR, camera_id, date_str)
        os.makedirs(dir_path, exist_ok=True)
        
        file_name = f"{time_str}.webp"
        full_path = os.path.join(dir_path, file_name)
        
        relative_path = os.path.relpath(full_path, start=STATIC_DIR)
        universal_relative_path = relative_path.replace("\\", "/")
        
        return full_path, universal_relative_path
    except Exception as e:
        logger.error(f"Failed to generate snapshot path for camera {camera_id}: {e}")
        return None, None


def take_snapshot(camera: Camera, db: Session) -> dict:
    """Main function to orchestrate taking a snapshot, trying HTTP first."""
    if not is_reachable(camera.ip):
        msg = f"⚠️ [{camera.hostname}] unreachable (ping failed)"
        logger.warning(msg)
        return error_response(camera.hostname, "Camera offline")

    result = try_http_snapshot(camera, db)
    if result["status"] == "success":
        return result

    logger.warning("Falling back to RTSP for %s", camera.hostname)
    return try_rtsp_snapshot(camera, db)


def try_http_snapshot(camera: Camera, db: Session) -> dict:
    """Attempts to take a snapshot via ONVIF HTTP GET."""
    try:
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        uri = cam.create_media_service().GetSnapshotUri({'ProfileToken': cam.create_media_service().GetProfiles()[0].token}).Uri
        
        response = try_auth(uri, camera.username, camera.password)
        response.raise_for_status()

        image_bytes = response.content
        if not image_bytes:
            raise RuntimeError("HTTP response was empty, no image data received.")
        
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("Failed to decode image data received via HTTP.")

        full_path, relative_path = generate_snapshot_path(str(camera.id))
        if not full_path:
            raise RuntimeError("Could not generate a valid file path for the snapshot.")
            
        height, width, _ = frame.shape
        resolution = f"{width}x{height}"
        
        # --- Construct multi-line watermark text ---
        config_text = get_config("watermark_text", default="Property of Company")
        timestamp_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        watermark_text = f"{config_text}" # | {timestamp_text}

        if not add_watermark_and_save_webp(frame, full_path, watermark_text, opacity=0.7):
            raise RuntimeError("Failed to apply watermark and save WebP file.")

        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)

        return success_response(camera, relative_path, resolution, "HTTP")

    except Exception as e:
        logger.warning(f"⚠️ HTTP snapshot failed for {camera.hostname}. Reason: {e}", exc_info=True)
        return error_response(camera.hostname, str(e))


def try_rtsp_snapshot(camera: Camera, db: Session) -> dict:
    """Attempts to take a snapshot via RTSP stream."""
    try:
        # BUG FIX: Create a new ONVIFCamera instance for this scope.
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        rtsp_uri = cam.create_media_service().GetStreamUri({'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}, 'ProfileToken': cam.create_media_service().GetProfiles()[0].token}).Uri
        if not rtsp_uri:
            raise RuntimeError("RTSP URL not available")

        frame = capture_frame_from_rtsp(rtsp_uri)
        if frame is None:
            raise RuntimeError("No valid frame received from RTSP stream.")

        full_path, relative_path = generate_snapshot_path(str(camera.id))
        if not full_path:
            raise RuntimeError("Could not generate a valid file path for the snapshot.")
            
        height, width, _ = frame.shape
        resolution = f"{width}x{height}"
        
        # --- Construct multi-line watermark text ---
        config_text = get_config("watermark_text", default="Property of Company")
        timestamp_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        watermark_text = f"{config_text}" # | {timestamp_text}"

        if not add_watermark_and_save_webp(frame, full_path, watermark_text, opacity=0.7):
            raise RuntimeError("Failed to apply watermark and save WebP file.")

        clean_old_snapshots(camera.hostname, db)
        check_stats.check_stats(camera)
        
        logger.info(f"✅ [{camera.hostname}] RTSP snapshot → {relative_path}")
        return success_response(camera, relative_path, resolution, "RTSP")

    except Exception as e:
        logger.exception(f"❌ [{camera.hostname}] RTSP snapshot failed definitively.")
        return error_response(camera.hostname, f"RTSP snapshot failed: {str(e)}")


def capture_frame_from_rtsp(rtsp_url: str, timeout: float = 10.0) -> np.ndarray | None:
    """Captures a single frame from an RTSP stream using OpenCV."""
    cap = cv2.VideoCapture(rtsp_url, apiPreference=cv2.CAP_FFMPEG)
    try:
        start_time = time.time()
        while not cap.isOpened():
            if time.time() - start_time > timeout:
                logger.error(f"Timeout opening RTSP stream: {rtsp_url}")
                return None
            time.sleep(0.1)
        
        for _ in range(5):
            cap.read()
        
        ret, frame = cap.read()
        if not ret or frame is None:
            logger.error("Failed to read a valid frame from RTSP stream.")
            return None
        return frame
    finally:
        if cap.isOpened():
            cap.release()

# --- Utility & Response Functions ---

def clean_old_snapshots(camera_name: str, db: Session):
    """Deletes old snapshot files and their database entries."""
    max_screenshots = int(get_config("max_screenshot_per_camera", default=10))
    if max_screenshots <= 0: return

    snapshots = db.query(Snapshot).filter(
        Snapshot.camera_name == camera_name
    ).order_by(Snapshot.timestamp.desc()).all()

    if len(snapshots) <= max_screenshots: return
    
    to_delete = snapshots[max_screenshots:]
    deleted_count = 0
    for snap in to_delete:
        try:
            base_path = os.path.splitext(os.path.join(STATIC_DIR, *snap.file_path.replace("\\", "/").split('/')))[0]
            webp_path = base_path + ".webp"
            jpg_path = base_path + ".jpg"

            for path in [webp_path, jpg_path]:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"Deleted old snapshot file: {path}")

            db.delete(snap)
            deleted_count += 1
        except Exception as e:
            logger.error(f"Failed to delete snapshot record {snap.id} or file: {e}")
    
    if deleted_count > 0:
        try:
            db.commit()
            logger.info(f"Committed deletion of {deleted_count} old snapshots from DB.")
        except Exception as e:
            logger.error(f"Failed to commit snapshot deletions: {e}")
            db.rollback()


def error_response(camera_name: str, error: str) -> dict:
    """Formats a consistent error response dictionary."""
    return {"status": "error", "message": f"Failed: {error}", "camera_name": camera_name}


def success_response(camera: Camera, file_path: str, resolution: str, method: str) -> dict:
    """Formats a consistent success response dictionary."""
    return {
        "status": "success",
        "message": f"Snapshot taken from {camera.hostname} via {method}",
        "camera_name": camera.hostname,
        "file_path": file_path,
        "resolution": resolution,
        "camera_ip": camera.ip
    }
