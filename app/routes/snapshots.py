import hashlib
import hmac
import os
from datetime import datetime
import time
from typing import Optional, List
from urllib.parse import quote, unquote # Import quote for URL encoding
from fastapi import APIRouter, Body, Query, Depends, HTTPException, Request
from fastapi.responses import FileResponse # Import FileResponse!
from sqlalchemy.orm import Session
from pydantic import BaseModel # Import BaseModel from pydantic
import re
from app.db.database import SessionLocal
from app.models_sql import Camera as DBCamera, Snapshot, User
from app.routes.auth import admin_access_required, user_access_required_optional
from app.utils.audit_logger import log_audit
from app.utils.snapshot_utils import SNAPSHOT_BASE_DIR  # points to app/static/snapshots
from app.utils.timezone_helper import to_current_timezone, format_datetime_with_tz

router = APIRouter(tags=["Snapshots API"])

# --- Pydantic Models for API Responses ---
class SnapshotResponse(BaseModel):
    """Response model for camera snapshots."""
    filename: str
    camera: str
    ip: str
    timestamp: str
    url: str
    img_path: str
    lat: str
    long: str

class LatestSnapshotDetailResponse(BaseModel):
    """Detail response model for the latest snapshot (metadata)."""
    status: str
    camera: str
    time: str
    url: str # This URL points to the actual image serving endpoint

def get_db():
    """Dependency to get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Helper Functions ---
def parse_snapshot_filename(filename: str) -> (Optional[str], Optional[datetime]):
    """
    Parses a snapshot filename to extract the camera name and a datetime object.
    Assumes filename format: <camera_name>_YYYYMMDD_HHMMSS.jpg
    Returns (camera_name, datetime_object) or (None, None) if parsing fails.
    """
    base_name = filename.replace(".jpg", "")
    # Split from the right, at most 2 times, to isolate the timestamp part
    parts = base_name.rsplit("_", 2)

    if len(parts) == 3:
        camera_name = parts[0]
        timestamp_str = f"{parts[1]}_{parts[2]}"
        try:
            dt = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            return camera_name, dt
        except ValueError:
            # In a production environment, use proper logging system
            import logging
            logging.warning("Could not parse timestamp from filename: %s", filename)
            return None, None
    else:
        import logging
        logging.warning("Unexpected filename format: %s", filename)
        return None, None

def get_snapshot_directory() -> str:
    """
    Returns the absolute path to the snapshot directory.
    Assumes the router file is in app/routers/ and snapshots are in app/static/snapshots/
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up from 'routers' to 'app', then up to project root, then into 'static/snapshots'
    # Assuming the structure is: project_root/app/routers/snapshots.py
    # and snapshots are in: project_root/app/static/images/snapshots/
    # This path assumes main.py is in project_root/app/
    # If main.py is in project_root/, then it's different.
    # Let's assume project_root/app/static/images/snapshots
    # current_dir: /path/to/project_root/app/routers
    # os.path.join(current_dir, ".."): /path/to/project_root/app
    # os.path.join(current_dir, "..", "static", "images", "snapshots"): /path/to/project_root/app/static/images/snapshots
    snapshot_path = os.path.join(current_dir, "..", "static", "snapshots")
    return os.path.normpath(snapshot_path)

SECRET_KEY = os.getenv("SECRET_KEY")


def generate_signed_token(file_path: str, expires_in: int = 300) -> str:
    """
    Generate a signed token that expires in `expires_in` seconds.
    """
    expires_at = int(time.time()) + expires_in
    payload = f"{file_path}|{expires_at}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def is_valid_signed_token(file_path: str, token: str) -> bool:
    try:
        expires_at_str, signature = token.split(".")
        expires_at = int(expires_at_str)
        if time.time() > expires_at:
            return False
        payload = f"{file_path}|{expires_at}"
        expected_signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected_signature)
    except Exception:
        return False
    

# Refactored code
# --- Search Latest Snapshot per Matching Camera ---
@router.get("/snapshots/search", response_model=List[SnapshotResponse])
def search_snapshots(
    identifier: Optional[str] = Query(None, description="Camera name prefix or IP address"),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    # ... (logika query kamera tetap sama)
    query = db.query(DBCamera)
    if identifier:
        identifier = identifier.strip()
        if len(identifier) < 2:
            raise HTTPException(status_code=400, detail="Search input must be at least 2 characters.")
        is_ip = re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", identifier)
        if is_ip:
            query = query.filter(DBCamera.ip == identifier)
        else:
            query = query.filter(DBCamera.hostname.ilike(f"{identifier}%"))
    cameras = query.all()
    if not cameras:
        return []
    
    camera_id_to_info = {str(cam.id): {"hostname": cam.hostname, "ip": cam.ip, "lat": str(cam.latitude or None), "long": str(cam.longitude or None)} for cam in cameras}
    camera_ids = list(camera_id_to_info.keys())

    snapshots = (db.query(Snapshot).filter(Snapshot.camera_id.in_(camera_ids)).order_by(Snapshot.timestamp.desc()).all())

    latest_snapshot_per_camera = {}
    for snap in snapshots:
        if snap.camera_id not in latest_snapshot_per_camera:
            cam_info = camera_id_to_info.get(snap.camera_id, {})
            
            # --- PERBAIKAN ZONA WAKTU ---
            # Gunakan helper dinamis untuk mengonversi dan memformat waktu
            timestamp_local = to_current_timezone(snap.timestamp, db)
            formatted_timestamp = format_datetime_with_tz(timestamp_local)

            latest_snapshot_per_camera[snap.camera_id] = SnapshotResponse(
                filename=os.path.basename(snap.file_path),
                camera=snap.camera_name,
                ip=snap.camera_ip,
                timestamp=formatted_timestamp, # ⬅️ Sekarang sudah menggunakan zona waktu dinamis
                url=f"/snapshot/file/{quote(snap.file_path)}",
                img_path=f"{quote(snap.file_path)}",
                lat=cam_info.get("lat", None),
                long=cam_info.get("long", None)
            )

    return list(latest_snapshot_per_camera.values())


@router.get("/snapshot/latest/{identifier}/info", response_model=SnapshotResponse)
def get_latest_snapshot_info(identifier: str, db: Session = Depends(get_db)):
    if len(identifier) < 2:
        raise HTTPException(status_code=400, detail="Identifier must be at least 2 characters.")

    is_ip = re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", identifier)
    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.camera_ip == identifier if is_ip else Snapshot.camera_name == identifier)
        .order_by(Snapshot.timestamp.desc())
        .first()
    )

    if not snapshot:
        raise HTTPException(status_code=404, detail=f"No snapshot found for '{identifier}'.")

    # --- PERBAIKAN ZONA WAKTU ---
    # Gunakan helper dinamis untuk mengonversi dan memformat waktu
    timestamp_local = to_current_timezone(snapshot.timestamp, db)
    formatted_timestamp = format_datetime_with_tz(timestamp_local)
    
    latitude = str(snapshot.camera.latitude) if snapshot.camera and snapshot.camera.latitude else ""
    longitude = str(snapshot.camera.longitude) if snapshot.camera and snapshot.camera.longitude else ""

    return SnapshotResponse(
        filename=os.path.basename(snapshot.file_path),
        camera=snapshot.camera_name,
        ip=snapshot.camera_ip,
        timestamp=formatted_timestamp, # ⬅️ Sekarang sudah menggunakan zona waktu dinamis
        url=f"/snapshot/file/{quote(snapshot.file_path)}",
        img_path=f"{quote(snapshot.file_path)}",
        lat=latitude,
        long=longitude
    )


# --- Serve File or Metadata ---
@router.get("/snapshot/file/{file_path:path}")
def get_snapshot_file_raw(
    request: Request,
    file_path: str,
    db: Session = Depends(get_db),
    token: Optional[str] = Query(default=None),
    user_phone: str = Query(default=None),
    group: str = Query(default=None),
    current_user: Optional[User] = Depends(user_access_required_optional)
):

    file_path = unquote(file_path)

    # Anti path traversal
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    # Cek apakah user login atau punya signed token
    if not current_user and not token:
        raise HTTPException(status_code=401, detail="Authentication required")

    if token and not is_valid_signed_token(file_path, token):
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    snapshot = db.query(Snapshot).filter(Snapshot.file_path == file_path).first()
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found")

    full_path = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Snapshot file not found")

    # Logging
    user_from_param = user_phone if user_phone else None
    if user_from_param:
        group_id = group
        final_user_name, extra = user_from_param, f"via Whatsapp Bot [group ID: {group_id}]"
    else:
        final_user_name = request.session.get("user_name") or "token_user"
        extra = "via token" if token else "via dashboard"

    log_audit(
        db=db,
        user=final_user_name,
        action="retrieve_snapshot",
        target=snapshot.camera_name,
        ip=request.client.host,
        extra=extra
    )

    return FileResponse(path=full_path, media_type="image/jpeg")


@router.get("/snapshot/token/{file_path:path}")
def get_signed_token(file_path: str, expires_in: int = 600):
    try:
        file_path = unquote(file_path)

        token = generate_signed_token(file_path, expires_in=expires_in)
        return {"token": token}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Token generation failed")
    

@router.post("/snapshot/token/batch")
def generate_tokens_batch(
    request: Request,
    files: List[str] = Body(...),
    expires_in: int = 300
):
    result = {}
    for file_path in files:
        token = generate_signed_token(file_path, expires_in)
        result[file_path] = token
    return result
