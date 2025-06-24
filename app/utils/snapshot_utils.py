import os
from datetime import datetime
from io import BytesIO
from PIL import Image
from sqlalchemy.orm import Session
from app.models_sql import Snapshot

# BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # 'app' folder
SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")  # absolut path

def get_image_resolution(image_bytes: bytes) -> str:
    with Image.open(BytesIO(image_bytes)) as img:
        width, height = img.size
    return f"{width}x{height}"

def record_snapshot_metadata(
    db: Session,
    camera_id: str,
    file_path: str,
    resolution: str
) -> Snapshot:
    from app.models_sql import Snapshot, Camera

    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise ValueError("Camera not found")

    abs_file_path = os.path.join(SNAPSHOT_BASE_DIR, file_path)
    if not os.path.exists(abs_file_path):
        raise FileNotFoundError(f"Snapshot file not found: {abs_file_path}")

    file_size = os.path.getsize(abs_file_path)

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
        timestamp=datetime.now()
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot
