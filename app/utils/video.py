import os
import logging
import asyncio
import subprocess
import uuid
from datetime import datetime, timezone
from fastapi import Request
from sqlalchemy import text
from app.core.logging_config import setup_logging
from app.models.camera import Camera
from app.models.video import Video
from app.utils.camera_onvif import get_rtsp_url
from app.utils.network_utils import is_reachable
from typing import Tuple, Dict, Any
import json
from app.db.database import SessionLocal
from pathlib import Path
from app.utils.audit_logger import log_audit
from app.ws.manager import get_ws_connections
import tempfile
from PIL import Image, ImageStat

# --- Basic Configuration ---
STATIC_VIDEO_DIR = Path("static/videos")
os.makedirs(STATIC_VIDEO_DIR, exist_ok=True)

# --- Logger Setup ---
setup_logging()
logger = logging.getLogger("snapshot")

def _run_ffmpeg_sync(cmd: str) -> Tuple[int, str, str]:
    logger.info("Running FFMPEG command: %s", cmd)
    proc = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore'
    )
    if proc.returncode != 0:
        logger.error("FFMPEG Error (return code %d):\nSTDERR: %s", proc.returncode, proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr


import tempfile
from PIL import Image, ImageStat

def get_frame_brightness(rtsp_url: str) -> float:
    """Ambil satu frame dari RTSP untuk hitung kecerahan rata-rata (0–255)."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name

        # Ambil 1 frame di 0.5 detik pertama
        subprocess.run([
            "ffmpeg", "-rtsp_transport", "tcp",
            "-y", "-i", rtsp_url,
            "-frames:v", "1", "-ss", "0.5",
            "-q:v", "5", tmp_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 10_000:
            raise RuntimeError("Frame capture failed or file too small")

        img = Image.open(tmp_path).convert("L")
        stat = ImageStat.Stat(img)
        brightness = stat.mean[0]
        img.close()
        return brightness
    except Exception as e:
        logger.warning(f"[Watermark] Gagal ambil frame brightness dari RTSP: {e}")
        return 128.0  # fallback netral
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def build_ffmpeg_watermark_cmd(rtsp_url: str, output_path: Path, duration: int, camera_name: str) -> str:
    """Buat command ffmpeg dengan watermark Liberation Sans, posisi tengah, warna adaptif & fallback aman."""
    # --- Ambil teks watermark dari config ---
    watermark_text = get_config_value("watermark_text", "Property of B-SNAP")
    text_combined = f"{watermark_text}"

    # --- Ambil resolusi kamera via ffprobe ---
    width, height = 1920, 1080  # default fallback
    try:
        meta_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", rtsp_url
        ]
        meta_proc = subprocess.run(meta_cmd, capture_output=True, text=True, timeout=5)
        parts = meta_proc.stdout.strip().split(",")
        if len(parts) == 2:
            width, height = [int(x) for x in parts]
    except Exception as e:
        logger.warning(f"[Watermark] Gagal ambil resolusi kamera: {e}")

    # --- Hitung font size agar lebar teks ~70% dari frame width ---
    char_estimate = max(len(text_combined) * 0.6, 1)
    font_size = max(18, int(width * 0.7 / char_estimate))

    # --- Ambil brightness frame untuk warna adaptif ---
    brightness = get_frame_brightness(rtsp_url)

    # --- Warna adaptif + fallback ---
    if brightness < 100:
        fontcolor = "white@0.9"
        bordercolor = "black@0.8"
    elif brightness > 160:
        fontcolor = "black@0.8"
        bordercolor = "white@0.9"
    else:
        fontcolor = "gray@0.8"
        bordercolor = "white@0.9"

    logger.info(
        f"[Watermark] camera={camera_name}, brightness={brightness:.1f}, "
        f"font={font_size}px, color={fontcolor}, border={bordercolor}"
    )

    # --- Path font Liberation Sans (sama seperti watermark foto) ---
    font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    if not os.path.exists(font_path):
        # fallback kalau font belum terinstall
        logger.warning("[Watermark] LiberationSans-Regular.ttf tidak ditemukan, fallback ke DejaVuSans.")
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    # --- drawtext di tengah frame ---
    drawtext = (
        f"drawtext=fontfile={font_path}:"
        f"text='{text_combined}':"
        f"fontsize={font_size}:fontcolor={fontcolor}:"
        f"bordercolor={bordercolor}:borderw=2:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )

    # --- Command ffmpeg final ---
    return (
        f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" -t {duration} '
        f'-vf "{drawtext}" '
        f'-c:v libx264 -preset veryfast -crf 23 -an "{output_path}"'
    )


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
        logger.error("Failed to get metadata for %s: %s", file_path, e)
        return {
            "duration": 0,
            "size": size,
            "width": 0,
            "height": 0
        }

def generate_thumbnail(video_path: str, output_thumb_path: str):
    try:
        subprocess.run([
            "ffmpeg",
            "-ss", "00:00:01",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            "-update", "1",
            output_thumb_path
        ], check=True)
        logger.info("🖼️ Thumbnail generated: %s", output_thumb_path)
        return True
    except subprocess.CalledProcessError as e:
        logger.warning("❌ Failed to generate thumbnail for %s: %s", video_path, e)
        return False


def get_config_value(key: str, default: str = "") -> str:
    db = SessionLocal()
    try:
        from app.models.config import Configuration
        item = db.query(Configuration).filter_by(key=key).first()
        return item.value if item else default
    except Exception as e:
        logger.warning(f"⚠️ Failed getting config '{key}': {e}")
        return default
    finally:
        db.close()

def detect_codec(rtsp_url: str) -> str:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                rtsp_url
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        logger.warning("Failed to detect codec for %s: %s", rtsp_url, e)
        return "unknown"

async def record_video_and_save_db(
        request: Request,
        camera_id: str,
        duration: int = 10
) -> Dict[str, Any]:
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
        logger.error("Camera with ID %s not found.", camera_id)
        return {"status": "error", "message": "Camera not found"}

    user_name = request.session.get("user_name", "unknown")
    client_ip = request.client.host if request.client else "unknown"

    if not camera.group or not camera.group.name:
        logger.error("Camera '%s' does not have a group. Cannot determine storage folder.", camera.hostname)
        return {"status": "error", "message": "Camera has no group"}

    reachable = await asyncio.to_thread(is_reachable, camera.ip)
    if not reachable:
        msg = f"⚠️ [{camera.hostname}] unreachable (ping failed), skipping"
        logger.warning("%s", msg)
        return {"status": "error", "message": "Camera Offline"}

    group_name = camera.group.name
    camera_name = camera.hostname

    video_directory = STATIC_VIDEO_DIR / group_name / camera_name
    os.makedirs(video_directory, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{camera_name}_{timestamp}.mp4"
    output_path = video_directory / filename
    db_file_path = (Path(group_name) / camera_name / filename).as_posix()

    if output_path.exists():
        logger.warning("Deleting leftover file before recording: %s", output_path)
        output_path.unlink()

    rtsp_url = get_rtsp_url(camera)
    if not rtsp_url:
        logger.error("Failed to get RTSP URL for camera %s", camera.hostname)
        return {"status": "error", "message": "Failed to get RTSP URL"}

    codec = await asyncio.to_thread(detect_codec, rtsp_url)

    temp_output = output_path
    if codec in ["hevc", "h265"]:
        temp_output = output_path.with_suffix(".mkv")

    apply_watermark = True  # nanti bisa dibuat per kamera kalau mau

    if apply_watermark:
        cmd = build_ffmpeg_watermark_cmd(rtsp_url, output_path, duration, camera_name)
        logger.info(f"Running watermark ffmpeg: {cmd}")
    else:
        if codec == "h264":
            cmd = f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" -t {duration} -c:v copy -an "{temp_output}"'
        elif codec in ["hevc", "h265"]:
            cmd = f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" -t {duration} -c:v copy -an "{temp_output}"'
        else:
            cmd = f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" -t {duration} -c:v libx264 -preset veryfast -crf 23 -an "{temp_output}"'

    code, _, err = await asyncio.to_thread(_run_ffmpeg_sync, cmd)

    if code != 0 and codec in ["hevc", "h265"]:
        logger.warning("Remux failed for %s, falling back to re-encode...", camera.hostname)
        cmd_fallback = f'ffmpeg -y -rtsp_transport tcp -i "{rtsp_url}" -t {duration} -c:v libx265 -preset ultrafast -crf 28 -an "{output_path}"'
        code, _, err = await asyncio.to_thread(_run_ffmpeg_sync, cmd_fallback)

        # Clean up failed MKV if exists
        if temp_output.exists():
            logger.info("Removing failed MKV remux: %s", temp_output)
            temp_output.unlink()
    elif temp_output != output_path:
        # Rename MKV to MP4 for consistency
        if temp_output.exists():
            temp_output.rename(output_path)
            logger.info("Renamed %s to %s", temp_output.name, output_path.name)

    if code != 0:
        logger.error("%s - FFMPEG Failed: %s", camera.hostname, err)
        return {"status": "error", "message": "FFMPEG process failed"}

    logger.info("Video recorded successfully: %s", output_path)

    metadata = get_video_metadata(str(output_path))
    if metadata.get("duration", 0) == 0:
        logger.warning("⚠️ Video from %s may be corrupted or too short (duration=0)", camera.hostname)

    if metadata.get("duration", 0) > 0:
        thumb_path = output_path.with_suffix(".jpg")
        success_thumb = await asyncio.to_thread(generate_thumbnail, str(output_path), str(thumb_path))
        if not success_thumb:
            logger.warning("⚠️ Thumbnail generation failed for %s", output_path)

    # P0-001: Calculate file hash for integrity verification
    file_hash = None
    try:
        import hashlib
        sha256_hash = hashlib.sha256()
        with open(output_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        file_hash = sha256_hash.hexdigest()
        logger.info("SHA-256 hash calculated for video %s: %s...", filename, file_hash[:16])
    except Exception as e:
        logger.warning("Failed to calculate hash for video %s: %s", filename, e)

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
        "resolution": f"{metadata.get('width')}x{metadata.get('height')}",
        "file_hash": file_hash,  # P0-001: Store file hash
    }

    def save_video_record_sync(data: Dict[str, Any], user_name: str, ip: str) -> bool:
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
            db.commit()

            log_audit(
                db=db,
                user=user_name,
                action="create_video_record",
                target=data['camera_name'],
                ip=ip,
                extra="via cameras menu"
            )

            logger.info("📢 PostgreSQL NOTIFY sent: %s", payload)
            return True
        except Exception as e:
            db.rollback()
            logger.error("Failed to save video metadata to DB for %s: %s", data['camera_name'], e)
            return False
        finally:
            db.close()

    success = await asyncio.to_thread(save_video_record_sync, video_data, user_name, client_ip)

    if success:
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
            "message": "Video from %s was recorded and saved successfully." % camera.hostname,
        }
    else:
        if os.path.exists(output_path):
            os.remove(output_path)
        return {"status": "error", "message": "Failed to save metadata to the database"}
