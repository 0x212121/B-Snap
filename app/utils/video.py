import os
import logging
import asyncio
import subprocess
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.logging_config import setup_logging
from app.models_sql import Camera, Video
from app.onvif_client import get_rtsp_url, is_reachable
from typing import Tuple, Dict, Any
import json
from app.db.database import SessionLocal
from pathlib import Path
from app.ws.manager import get_ws_connections

# --- Basic Configuration ---
# Using pathlib for more modern and robust path handling.
# This assumes this file is in app/utils/, so Path(__file__).resolve().parents[1] is the 'app/' directory.
# APP_DIR = Path(__file__).resolve().parents[1]
STATIC_VIDEO_DIR = Path("static/videos")
os.makedirs(STATIC_VIDEO_DIR, exist_ok=True)

# ... [imports tetap sama seperti sebelumnya] ...

# --- Logger Setup ---
setup_logging()
logger = logging.getLogger("snapshot")

def _run_ffmpeg_sync(cmd: str) -> Tuple[int, str, str]:
    logger.info(f"Running FFMPEG command: {cmd}")
    proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )
    if proc.returncode != 0:
        logger.error(f"FFMPEG Error (return code {proc.returncode}):\nSTDERR: {proc.stderr}")
    return proc.returncode, proc.stdout, proc.stderr


def get_video_metadata(file_path: str) -> Dict[str, Any]:
    if not os.path.exists(file_path):
        return {"duration": 0, "size": 0, "width": 0, "height": 0}
        
    size = os.path.getsize(file_path)
    cmd = f'ffprobe -v quiet -print_format json -show_format -show_streams "{file_path}"'

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        duration = float(data.get('format', {}).get('duration', 0))

        width = height = 0
        for stream in data.get('streams', []):
            if stream.get("codec_type") == "video":
                width = stream.get("width", 0)
                height = stream.get("height", 0)
                break

        return {
            "duration": int(duration),
            "size": size,
            "width": width,
            "height": height
        }

    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to get metadata for {file_path}: {e}")
        return {
            "duration": 0,
            "size": size,
            "width": 0,
            "height": 0
        }


async def record_video_and_save_db(camera_id: str, duration: int = 10) -> Dict[str, Any]:
    def get_camera_sync() -> Camera:
        db = SessionLocal()
        try:
            camera = db.query(Camera).filter(Camera.id == camera_id).first()
            if camera and camera.group:
                _ = camera.group.name
            return camera
        finally:
            db.close()

    camera = await asyncio.to_thread(get_camera_sync)
    
    if not camera:
        logger.error(f"Camera with ID {camera_id} not found.")
        return {"status": "error", "message": "Camera not found"}
        
    if not camera.group or not camera.group.name:
        logger.error(f"Camera '{camera.hostname}' does not have a group. Cannot determine storage folder.")
        return {"status": "error", "message": "Camera has no group"}

    reachable = await asyncio.to_thread(is_reachable, camera.ip)
    if not reachable:
        msg = f"⚠️ [{camera.hostname}] unreachable (ping failed), skipping"
        logger.warning(msg)
        return {"status": "error", "message": "Camera Offline"}

    group_name = camera.group.name
    camera_name = camera.hostname

    video_directory = STATIC_VIDEO_DIR / group_name / camera_name
    os.makedirs(video_directory, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{camera_name}_{timestamp}.mp4"
    output_path = video_directory / filename
    db_file_path = (Path(group_name) / camera_name / filename).as_posix()

    # 🧹 Clean leftover file if exists
    if output_path.exists():
        logger.warning(f"Deleting leftover file before recording: {output_path}")
        output_path.unlink()

    rtsp_url = get_rtsp_url(camera)
    if not rtsp_url:
        logger.error(f"Failed to get RTSP URL for camera {camera.hostname}")
        return {"status": "error", "message": "Failed to get RTSP URL"}

    cmd_copy = (
        f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" '
        f'-t {duration} -c:v copy -an "{str(output_path)}"'
    )
    code, _, err = await asyncio.to_thread(_run_ffmpeg_sync, cmd_copy)

    if code != 0:
        logger.warning(f"Remux failed for {camera.hostname}, falling back to re-encode...")
        cmd_reencode = (
            f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" '
            f'-t {duration} -c:v libx264 -preset veryfast -crf 23 -an "{str(output_path)}"'
        )
        code, _, err = await asyncio.to_thread(_run_ffmpeg_sync, cmd_reencode)
        if code != 0:
            logger.error("%s - FFMPEG Re-encode Failed: %s", camera.hostname, err)
            return {"status": "error", "message": "FFMPEG process failed"}

    logger.info(f"Video recorded successfully: {output_path}")

    metadata = get_video_metadata(str(output_path))
    if metadata.get("duration", 0) == 0:
        logger.warning(f"⚠️ Video from {camera.hostname} may be corrupted or too short (duration=0)")

    video_data = {
        "id": str(uuid.uuid4()),
        "camera_id": camera.id,
        "camera_name": camera.hostname,
        "camera_ip": camera.ip,
        "camera_group": group_name,
        "timestamp": datetime.now(timezone.utc),
        "file_path": db_file_path,
        "file_size": metadata.get("size"),
        "duration": metadata.get("duration"),
        "resolution": f"{metadata.get('width')}x{metadata.get('height')}"
    }

    def save_video_record_sync(data: Dict[str, Any]) -> bool:
        db = SessionLocal()
        try:
            new_video_record = Video(**data)
            db.add(new_video_record)

            payload = json.dumps({
                "type": "record_complete",
                "camera_name": data["camera_name"],
                "group": data["camera_group"],
                "duration": data["duration"],
                "file_size": data["file_size"],
                "file_path": data["file_path"],
                "source": "db_trigger"
            })
            db.execute(text("NOTIFY camera_notifications, :payload"), {"payload": payload})
            db.commit()  # ✅ satu kali commit untuk add + notify

            logger.info(f"📢 PostgreSQL NOTIFY sent: {payload}")
            return True
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save video metadata to DB for {data['camera_name']}: {e}")
            return False
        finally:
            db.close()

    success = await asyncio.to_thread(save_video_record_sync, video_data)

    if success:
        # ✅ Broadcast ke WebSocket dengan asyncio.gather()
        connections = get_ws_connections()
        message = {
            "type": "record_complete",
            "camera_name": camera.hostname,
            "group": group_name,
            "duration": metadata.get("duration"),
            "file_size": metadata.get("size"),
            "file_path": db_file_path
        }

        await asyncio.gather(*[
            ws.send_text(json.dumps(message)) for ws in connections.copy()
        ], return_exceptions=True)

        return {
            "status": "success",
            "message": f"Video from {camera.hostname} was recorded and saved successfully.",
        }
    else:
        if os.path.exists(output_path):
            os.remove(output_path)
        return {"status": "error", "message": "Failed to save metadata to the database"}
