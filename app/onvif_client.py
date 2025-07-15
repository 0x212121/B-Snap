import logging
from onvif import ONVIFCamera
import os
import subprocess
import platform
import requests
import cv2
import time
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from app.models_sql import Camera
from app.db.database import SessionLocal
from urllib.parse import urlparse, urlunparse, quote
from PIL import Image, ImageDraw, ImageFont
from app.core.logging_config import setup_logging
from ping3 import ping
import numpy as np
import traceback

# Setup logging
setup_logging()
logger = logging.getLogger("snapshot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static", "images")


def is_reachable(ip: str, timeout: int = 1000) -> bool:
    """
    Ping the given IP once. Return True if reachable.
    Timeout is in milliseconds.
    """
    try:
        # ping3 expects timeout in seconds
        timeout_seconds = timeout / 1000
        # The ping function returns the delay in seconds on success, or False on timeout.
        response = ping(ip, timeout=timeout_seconds)
        return isinstance(response, float)
    except Exception as e:
        logger.error(f"Error pinging {ip}: {e}")
        return False


def try_auth(uri, username, password):
    """Tries Digest and then Basic authentication for a given URI."""
    try:
        # logger.info("🔐 Trying Digest Auth...")
        r = requests.get(uri, auth=HTTPDigestAuth(username, password), timeout=5)
        if r.status_code == 401:
            logger.warning(f"⚠️ Digest failed with status {r.status_code}, trying Basic...")
            r = requests.get(uri, auth=HTTPBasicAuth(username, password), timeout=5)
        r.raise_for_status()  # Raises an HTTPError for bad responses (4xx or 5xx)
        return r
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Auth failed: {e}")


def stream_exists_opencv(rtsp_url: str, timeout: float = 5.0) -> bool:
    """Checks if an RTSP stream is accessible using OpenCV."""
    start = time.time()
    cap = cv2.VideoCapture(rtsp_url)

    # loop until opened or timeout
    while not cap.isOpened():
        if time.time() - start > timeout:
            cap.release()
            return False
        # small sleep so we don't busy-spin
        time.sleep(0.1)

    cap.release()
    return True


def load_active_cameras():
    """Get all cameras with status 'Active' from DB"""
    db = SessionLocal()
    try:
        cameras = db.query(Camera).filter(Camera.status == "Active").all()
        return cameras
    finally:
        db.close()


def capture_frame_from_rtsp(rtsp_url: str, timeout: float = 10.0) -> np.ndarray | None:
    """
    Captures a single frame from an RTSP stream using OpenCV.
    
    Args:
        rtsp_url: The URL of the RTSP stream.
        timeout: Time in seconds to wait for the stream to open.

    Returns:
        A NumPy array representing the captured frame in BGR format, or None on failure.
    """
    logger.info(f"Attempting to capture frame from {rtsp_url}")
    cap = cv2.VideoCapture(rtsp_url)
    
    start_time = time.time()
    while not cap.isOpened():
        if time.time() - start_time > timeout:
            logger.error(f"Timeout: Could not open video stream {rtsp_url} after {timeout} seconds.")
            cap.release()
            return None
        time.sleep(0.1)

    ret, frame = cap.read()
    cap.release()

    if not ret:
        logger.error(f"Failed to read frame from stream {rtsp_url}. 'ret' was {ret}.")
        return None
    
    logger.info(f"Successfully captured frame from {rtsp_url}.")
    return frame


def add_watermark_and_save_webp(frame: np.ndarray, output_path: str, text: str, opacity=0.5, color=(128, 128, 128), outline_color=(255, 255, 255), outline_width=1):
    """
    Adds a watermark to an image frame (from OpenCV) and saves it directly as a WEBP file,
    avoiding intermediate JPG files.

    Args:
        frame: The input image as a NumPy array (in BGR format from OpenCV).
        output_path: The full path to save the output WEBP file (e.g., /path/to/image.webp).
        text: The watermark text.
        opacity: The opacity of the watermark text (0.0 to 1.0).
        color: The main color of the text.
        outline_color: The color of the text outline.
        outline_width: The width of the text outline.
        
    Returns:
        The path to the saved WEBP file, or None on failure.
    """
    try:
        # Ensure the output path has the .webp extension
        output_path_webp = os.path.splitext(output_path)[0] + ".webp"

        # Convert OpenCV's BGR frame to an RGBA PIL Image
        base_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
        
        # Create a transparent layer for the text
        temp_image = Image.new("RGBA", base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(temp_image)

        # Font selection with fallback
        try:
            # A common path for a sans-serif font on Linux
            font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
            font_size = int(min(base_image.size) * 0.05) # Dynamic font size
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning(f"Font at {font_path} not found. Falling back to default font.")
            font = ImageFont.load_default()

        # Calculate text position to center it
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (base_image.width - text_width) / 2
        y = (base_image.height - text_height) / 2

        # Prepare colors with opacity
        fill_main = color + (int(255 * opacity),)
        fill_outline = outline_color + (int(255 * opacity),)

        # Draw outline by drawing text at slightly offset positions
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx != 0 or dy != 0: # Don't draw center position yet
                    draw.text((x + dx, y + dy), text, font=font, fill=fill_outline)
        
        # Draw the main text on top
        draw.text((x, y), text, font=font, fill=fill_main)

        # Composite the text layer onto the base image
        watermarked = Image.alpha_composite(base_image, temp_image)

        # Save the final watermarked image as WEBP. 
        # No need for .convert("RGB") as WEBP supports alpha, preserving the watermark's transparency.
        watermarked.save(output_path_webp, "WEBP", quality=85)

        logger.info(f"Watermarked snapshot saved to {output_path_webp}")
        return output_path_webp

    except Exception as e:
        logger.error(f"Failed to watermark and save frame: {e}\n{traceback.format_exc()}")
        return None
    

def add_watermark(image_path, text, opacity=0.5, color=(128, 128, 128), outline_color=(255, 255, 255), outline_width=1):
    """
    Add text watermark with outline to image with specific transparency and color.
    """
    try:
        base_image = Image.open(image_path).convert("RGBA")
        temp_image = Image.new("RGBA", base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(temp_image)

        try:
            # Use Liberation Sans (installed via fonts-liberation in Docker)
            font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
            font_size = int(min(base_image.size) * 0.05)  # Font size ~5% of smallest dimension
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning("Liberation Sans font not found, falling back to default font.")
            font = ImageFont.load_default()

        # Calculate text position in center using textbox
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[-2] - bbox[-4]  # right - left
        text_height = bbox[-1] - bbox[-3]  # bottom - top

        x = (base_image.width - text_width) / 2
        y = (base_image.height - text_height) / 2

        # Colored text with opacity
        text_color = color + (int(255 * opacity),)  # RGBA
        outline_fill = outline_color + (int(255 * opacity),)  # RGBA for outline

        # Draw outline by drawing text slightly offset in all directions
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=outline_fill)

        # Draw main text
        draw.text((x, y), text, font=font, fill=text_color)

        # Merge base image with watermark layer
        watermarked_image = Image.alpha_composite(base_image, temp_image)

        # Save result as RGB (JPEG-friendly)
        watermarked_image.convert("RGB").save(image_path)

        logger.info(
            "Watermark added to %s with color %s, outline %s (width %s), and opacity %s",
            image_path, color, outline_color, outline_width, opacity
        )

    except Exception as e:
        logger.error("Failed to add watermark to %s: %s", image_path, e)


def get_rtsp_url(camera: Camera):
    """Get RTSP URL from ONVIF camera"""
    try:
        # Create object ONVIFCamera
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        media_service = cam.create_media_service()
        profiles = media_service.GetProfiles()
        if not profiles:
            logger.error(f"No media profiles found for camera {camera.name}")
            return None
        profile = profiles[0]

        encoded_user = quote(camera.username, safe='')
        encoded_pass = quote(camera.password, safe='')
        stream_setup = {
            'StreamSetup': {
                'Stream': 'RTP-Unicast',
                'Transport': {'Protocol': 'RTSP'}
            },
            'ProfileToken': profile.token
        }
        
        uri_response = media_service.GetStreamUri(stream_setup)
        uri = uri_response.Uri

        # Inject username/password if not present in the returned URI
        parsed = urlparse(uri)
        if not parsed.username and camera.username:
            netloc = f"{encoded_user}:{encoded_pass}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)
        
        final_uri = urlunparse(parsed)
        logger.info(f"Final RTSP URI for {camera.hostname}: {final_uri}")
        return final_uri
    except Exception as e:
        logger.error(f"Error getting RTSP URL for {camera.hostname}: {str(e)}")
        return None

# --- Example Usage ---
# This is how you would use the new functions in your main script logic.
# You would typically loop through your active cameras.

def main_snapshot_logic():
    """
    Example of the main logic to capture and save snapshots for all active cameras.
    """
    logger.info("Starting snapshot process...")
    cameras = load_active_cameras()
    if not os.path.exists(STATIC_DIR):
        os.makedirs(STATIC_DIR)

    for camera in cameras:
        logger.info(f"--- Processing camera: {camera.name} ({camera.ip}) ---")
        if not is_reachable(camera.ip):
            logger.warning(f"Camera {camera.name} is not reachable. Skipping.")
            continue

        rtsp_url = get_rtsp_url(camera)
        if not rtsp_url:
            logger.error(f"Could not get RTSP URL for {camera.name}. Skipping.")
            continue

        # 1. Capture frame directly into memory
        frame = capture_frame_from_rtsp(rtsp_url)

        if frame is not None:
            # 2. Watermark and save directly to .webp
            output_file = os.path.join(STATIC_DIR, f"camera_{camera.id}.webp")
            watermark_text = f"{camera.name} - {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            add_watermark_and_save_webp(
                frame=frame,
                output_path=output_file,
                text=watermark_text
            )
        else:
            logger.error(f"Failed to capture frame for {camera.name}. Skipping.")
    
    logger.info("Snapshot process finished.")

# To run this example, you would call main_snapshot_logic() from your script's entry point.
# if __name__ == "__main__":
#     main_snapshot_logic()
