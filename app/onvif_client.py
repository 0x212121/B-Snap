import logging
from onvif import ONVIFCamera
import os
import requests
import cv2
import time
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from app.models.camera import Camera
from app.db.database import SessionLocal
from urllib.parse import urlparse, urlunparse
from PIL import Image, ImageDraw, ImageFont
from app.core.logging_config import setup_logging
from ping3 import ping

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
        response = ping(ip, timeout=timeout_seconds)
        return response is not None
    except Exception:
        return False


def try_auth(uri, username, password):
    try:
        # logger.info("🔐 Trying Digest Auth...")
        r = requests.get(uri, auth=HTTPDigestAuth(username, password), timeout=5)
        if r.status_code == 401:
            logger.warning(f"⚠️ Digest failed with status {r.status_code}, trying Basic...")
            r = requests.get(uri, auth=HTTPBasicAuth(username, password), timeout=5)
        if not r.ok:
            raise RuntimeError(f"Auth HTTP {r.status_code}")
        return r
    except Exception as e:
        raise RuntimeError(f"Auth failed: {e}")
    

def stream_exists_opencv(rtsp_url: str, timeout: float = 5.0) -> bool:
    start = time.time()
    cap = cv2.VideoCapture()
    cap.open(rtsp_url)

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
    """Get all cameras with status 'Active' or 'Restricted' from DB"""
    db = SessionLocal()
    try:
        cameras = db.query(Camera).filter(Camera.status.in_(["Active", "Restricted"])).all()
        return cameras
    finally:
        db.close()


def add_watermark(image_path, text, opacity=0.5, color=(128, 128, 128), outline_color=(255, 255, 255), outline_width=1):
    """
    Add text watermark with outline to image with specific transparency and color.
    
    This function has been revised to explicitly close Image objects in a finally block
    to prevent potential memory leaks.
    """
    base_image = None
    temp_image = None
    watermarked_image = None
    
    try:
        # Open image and convert to RGBA
        base_image = Image.open(image_path).convert("RGBA")

        # Create a transparent overlay for the watermark
        temp_image = Image.new("RGBA", base_image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(temp_image)

        try:
            # Use Liberation Sans font if available
            font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
            font_size = int(min(base_image.size) * 0.05)
            font = ImageFont.truetype(font_path, font_size)
        except IOError:
            logger.warning("Liberation Sans font not found, falling back to default font.")
            font = ImageFont.load_default()

        # Calculate text position in center
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (base_image.width - text_width) / 2
        y = (base_image.height - text_height) / 2

        text_color = color + (int(255 * opacity),)
        outline_fill = outline_color + (int(255 * opacity),)

        # Draw outline
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), text, font=font, fill=outline_fill)

        # Draw main text
        draw.text((x, y), text, font=font, fill=text_color)

        # Merge base image with watermark layer
        watermarked_image = Image.alpha_composite(base_image, temp_image)

        # Save result, converting to RGB for JPEG compatibility
        watermarked_image.convert("RGB").save(image_path)

        logger.info(
            "Watermark added to %s with color %s, outline %s (width %s), and opacity %s",
            image_path, color, outline_color, outline_width, opacity
        )

    except Exception as e:
        logger.error("Failed to add watermark to %s: %s", image_path, e)
        # Re-raise the exception to be handled by the caller, e.g., run_snapshot
        raise
        
    finally:
        # IMPORTANT: Explicitly close all Pillow Image objects to free memory
        if base_image:
            base_image.close()
        if temp_image:
            temp_image.close()
        if watermarked_image:
            watermarked_image.close()


def get_rtsp_url(camera: Camera):
    """Get RTSP URL from ONVIF camera"""
    try:
        # Create object ONVIFCamera
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        media_service = cam.create_media_service()
        profiles = media_service.GetProfiles()
        profile = profiles[0]

        from urllib.parse import quote
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

        # Inject username/password if not exist
        parsed = urlparse(uri)
        if not parsed.username and camera.username:
            netloc = f"{encoded_user}:{encoded_pass}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)
        
        final_uri = urlunparse(parsed)
        return final_uri
    except Exception as e:
        logger.error(f"Error getting RTSP URL: {str(e)}")
        return None
