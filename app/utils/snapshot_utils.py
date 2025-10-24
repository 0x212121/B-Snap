import os
import logging
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image
import numpy as np
from sqlalchemy.orm import Session
from uuid import uuid4

from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.models.health import CameraHealth
from app.models.email_retry_queue import EmailRetryQueue
from app.utils.image_check import detect_blur, detect_brightness, detect_occlusion
from app.utils.email_notifier import send_tamper_alert, send_recovery_alert, queue_email_retry

logger = logging.getLogger("snapshot")

SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")

# === konfigurasi ambang ===
TAMPER_CONFIRM_THRESHOLD = 3    # 3 snapshot berturut-turut baru dianggap tampered
RECOVERY_CONFIRM_THRESHOLD = 2  # 2 snapshot normal berturut-turut dianggap pulih


def get_image_resolution(image_bytes: bytes) -> str:
    with Image.open(BytesIO(image_bytes)) as img:
        width, height = img.size
    return f"{width}x{height}"


def to_native_float(val):
    if isinstance(val, (np.float32, np.float64)):
        return float(val)
    return val


def record_snapshot_metadata(db: Session, camera_id: str, file_path: str, resolution: str) -> Snapshot:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise ValueError("Camera not found")

    abs_file_path = os.path.join(SNAPSHOT_BASE_DIR, file_path)
    if not os.path.exists(abs_file_path):
        raise FileNotFoundError(f"Snapshot file not found: {abs_file_path}")

    file_size = os.path.getsize(abs_file_path)
    with open(abs_file_path, "rb") as f:
        image_bytes = f.read()

    # === analisis citra ===
    is_blur, blur_score = detect_blur(image_bytes)
    is_brightness_issue, brightness_reason = detect_brightness(image_bytes)
    is_occluded, occlusion_metrics = detect_occlusion(image_bytes)

    is_tampered = is_blur or is_brightness_issue or is_occluded
    tamper_reasons = []
    if is_blur:
        tamper_reasons.append("blur")
    if is_brightness_issue:
        tamper_reasons.append(brightness_reason)
    if is_occluded:
        tamper_reasons.append("occluded")

    snapshot = Snapshot(
        camera_id=camera.id,
        camera_name=camera.hostname,
        camera_ip=camera.ip,
        camera_port=camera.port,
        camera_location=camera.location,
        camera_group=camera.group.name if camera.group else None,
        file_path=file_path,
        file_size=file_size,
        resolution=resolution,
        timestamp=datetime.now(timezone.utc),
        is_tampered=is_tampered,
        tamper_reason=", ".join(tamper_reasons) if tamper_reasons else None,
        blur_score=blur_score,
        entropy_score=to_native_float(occlusion_metrics["entropy"]),
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    # === update CameraHealth ===
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if not health:
        health = CameraHealth(camera_id=camera.id, status="Unknown")
        db.add(health)

    # update counter
    if is_tampered:
        health.consecutive_tamper = (health.consecutive_tamper or 0) + 1
        health.consecutive_normal = 0
    else:
        health.consecutive_normal = (health.consecutive_normal or 0) + 1
        health.consecutive_tamper = 0

    prev_status = health.tamper_status or "normal"

    # === pastikan status di-update eksplisit ===
    if health.consecutive_tamper >= TAMPER_CONFIRM_THRESHOLD:
        new_status = "tampered"
    elif health.consecutive_normal >= RECOVERY_CONFIRM_THRESHOLD:
        new_status = "normal"
    else:
        new_status = prev_status

    health.tamper_status = new_status

    # === Transisi: normal → tampered ===
    if prev_status != "tampered" and new_status == "tampered":
        try:
            send_tamper_alert(db, camera, snapshot.tamper_reason, abs_file_path)
            health.last_email_sent = datetime.now(timezone.utc)
            logger.warning("[ALERT] %s marked tampered (%s)", camera.hostname, snapshot.tamper_reason)
        except Exception as e:
            # Pastikan tidak duplikat
            existing_retry = db.query(EmailRetryQueue).filter(
                EmailRetryQueue.camera_id == camera.id,
                EmailRetryQueue.type == "tamper",
                EmailRetryQueue.sent == False
            ).first()
            if not existing_retry:
                queue_email_retry(db, camera, "tamper", reason=snapshot.tamper_reason, file_path=abs_file_path, delay_minutes=1)
            else:
                logger.info("[QUEUE] Skip duplicate tamper retry for %s", camera.hostname)
            logger.exception("[QUEUE] Tamper email failed for %s: %s", camera.hostname, e)

    # === Transisi: tampered → normal ===
    elif prev_status == "tampered" and new_status == "normal":
        try:
            send_recovery_alert(db, camera)
            health.last_email_sent = datetime.now(timezone.utc)
            logger.info("[RECOVERY] %s back to normal", camera.hostname)
        except Exception as e:
            existing_retry = db.query(EmailRetryQueue).filter(
                EmailRetryQueue.camera_id == camera.id,
                EmailRetryQueue.type == "recovery",
                EmailRetryQueue.sent == False
            ).first()
            if not existing_retry:
                queue_email_retry(db, camera, "recovery", delay_minutes=1)
            else:
                logger.info("[QUEUE] Skip duplicate recovery retry for %s", camera.hostname)
            logger.exception("[QUEUE] Recovery email failed for %s: %s", camera.hostname, e)

    # === pembaruan umum ===
    health.checked = datetime.now(timezone.utc)
    health.status_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(health)

    # === log tambahan ===
    if is_tampered:
        logger.warning("[TAMPER DETECTED] %s: %s (%.2f)", camera.hostname, snapshot.tamper_reason, blur_score)
    else:
        logger.info("[SNAPSHOT OK] %s – %.2f", camera.hostname, blur_score)

    return snapshot
