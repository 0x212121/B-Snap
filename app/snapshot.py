import logging
from app.utils.network_utils import is_reachable, try_auth, stream_exists_opencv
from app.utils.image_utils import add_watermark
from app.utils.camera_onvif import get_rtsp_url
from app.models.camera import Camera
from app.db.database import SessionLocal
from app.core.logging_config import setup_logging

logger = logging.getLogger("snapshot")

def load_active_cameras():
    db = SessionLocal()
    try:
        return db.query(Camera).filter(Camera.status.in_(["Active", "Restricted"])).all()
    finally:
        db.close()
