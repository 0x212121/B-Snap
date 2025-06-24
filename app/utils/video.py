import os
import logging
import asyncio
import subprocess
from datetime import datetime
from app.db.database import SessionLocal
from app.models_sql import Camera
from app.onvif_client import get_rtsp_url, is_reachable
from typing import Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_VIDEO_DIR = "app/static/videos"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log_camera_error(camera_name, message):
    filepath = os.path.join(LOG_DIR, f"{camera_name}_log.txt")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")

def get_camera_by_id(camera_id: int) -> Camera:
    """Mendapatkan informasi kamera berdasarkan ID"""
    db = SessionLocal()
    try:
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            logger.error(f"Camera with ID {camera_id} not found")
            return None
        return camera
    except Exception as e:
        logger.error(f"Error getting camera: {str(e)}")
        return None
    finally:
        db.close()

async def _run_cmd(cmd: str) -> Tuple[int, str, str]:
    """
    Run a shell command in a thread pool (so it's non‐blocking) and
    return (returncode, stdout, stderr).
    """
    loop = asyncio.get_event_loop()
    # run subprocess.run in the default ThreadPoolExecutor
    proc = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True  # text mode
        )
    )
    return proc.returncode, proc.stdout, proc.stderr

async def record_video_clip(camera_id: str, duration: int = 5) -> str:
    """
    Record `duration` seconds of H.264 from RTSP into an MP4 file via ffmpeg.
    Returns the output path or a status string on error.
    """
    camera = get_camera_by_id(camera_id)
    if not camera:
        return None

    if not is_reachable(camera.ip):
        msg = f"⚠️ [{camera.hostname}] unreachable (ping failed), skipping"
        logger.warning(msg)
        log_camera_error(camera.hostname, msg)
        return "Camera Offline"

    os.makedirs(STATIC_VIDEO_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{camera.hostname}_{timestamp}.mp4"
    output_path = os.path.join(STATIC_VIDEO_DIR, filename)

    rtsp_url = get_rtsp_url(camera)
    if not rtsp_url:
        logger.error(f"Failed to get RTSP URL for camera {camera.hostname}")
        return None

    # 1) Try remux H.264 without re-encode
    cmd_copy = (
        f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" '
        f'-t {duration} -c:v copy -c:a copy "{output_path}"'
    )
    code, out, err = await _run_cmd(cmd_copy)
    if code == 0:
        logger.info(f"[+] Video remuxed successfully: {output_path}")
    else:
        # 2) Fallback to re-encode with libx264 + aac audio
        logger.warning(f"[!] Remux failed for {camera.hostname}, fallback re-encode:\n{err}")
        cmd_reencode = (
            f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" '
            f'-t {duration} -c:v libx264 -preset veryfast -crf 23 '
            f'-c:a aac -b:a 128k "{output_path}"'
        )
        code2, out2, err2 = await _run_cmd(cmd_reencode)
        if code2 == 0:
            logger.info(f"[+] Video re-encoded successfully: {output_path}")
        else:
            logger.error(f"[✖] Re-encode failed for {camera.hostname}:\n{err2}")
            return None

    # send WebSocket notifications
    from app.main import websocket_connections
    from fastapi.websockets import WebSocketState

    msg = f"✅ Video clip ready: {camera.hostname} ({duration}s)"
    for ws in websocket_connections:
        if ws.client_state == WebSocketState.CONNECTED:
            try:
                await ws.send_json({"status": "success", "message": msg})
            except Exception as e:
                logger.error(f"WS send error: {e}")

    return output_path