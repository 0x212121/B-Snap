import os
from datetime import datetime, timezone
from io import BytesIO
from PIL import Image
import numpy as np
from sqlalchemy.orm import Session
from app.models.camera import Camera
from app.models.snapshot import Snapshot
from app.utils.image_check import detect_blur, detect_brightness, detect_occlusion
import logging

# BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # 'app' folder
SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")  # absolut path

def get_image_resolution(image_bytes: bytes) -> str:
    with Image.open(BytesIO(image_bytes)) as img:
        width, height = img.size
    return f"{width}x{height}"

def to_native_float(val):
    if isinstance(val, (np.float32, np.float64)):
        return float(val)
    return val

def record_snapshot_metadata(
    db: Session,
    camera_id: str,
    file_path: str,
    resolution: str
) -> Snapshot:

    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise ValueError("Camera not found")

    abs_file_path = os.path.join(SNAPSHOT_BASE_DIR, file_path)
    if not os.path.exists(abs_file_path):
        raise FileNotFoundError(f"Snapshot file not found: {abs_file_path}")

    file_size = os.path.getsize(abs_file_path)

    with open(abs_file_path, "rb") as f:
        image_bytes = f.read()

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
        entropy_score=to_native_float(occlusion_metrics["entropy"])
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    if is_tampered:
        logging.warning("[TAMPER] Detected on snapshot %s: %s", file_path, snapshot.tamper_reason)

    return snapshot
