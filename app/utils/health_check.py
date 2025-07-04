from datetime import datetime, timezone, timedelta
import time
from sqlalchemy import and_
from sqlalchemy.orm import Session
from app.models_sql import NVR, CameraHealth, Camera as DBCamera, HealthCheckStatus, CameraDailyStats
from app.db.database import SessionLocal
from ping3 import ping, errors
from app.core.logging_config import setup_logging
import logging

# setup logging
setup_logging()
logger = logging.getLogger("healthcheck")

PING_TIMEOUT = 1.0
PING_ATTEMPTS = 3


def ping_device(ip: str) -> tuple[bool, int | None]:
    """
    Pings a device multiple times and returns its status and average latency.
    A device is considered 'Online' if at least one ping attempt is successful.
    """
    latencies = []
    try:
        for _ in range(PING_ATTEMPTS):
            latency = ping(ip, timeout=PING_TIMEOUT, unit='s')
            if isinstance(latency, float):
                latencies.append(latency)
        
        if not latencies:
            return False, None
            
    except errors.DestinationHostUnreachable:
        return False, None
    except Exception as e:
        logger.error("An unexpected error occurred while pinging %s: %s", ip, e)
        return False, None
    
    avg_latency = sum(latencies) / len(latencies)
    return True, int(avg_latency * 1000)

# ==============================================================================
# 💡 NEW INTEGRATED FUNCTION
# ==============================================================================
def _perform_and_update_health_check(db: Session, device_info: dict) -> tuple[str, int | None]:
    """
    Perform a health check on a single device, update its status, and accurately calculate
    daily uptime/downtime statistics. This is the core function used by all ping processes.
    """
    device_id = device_info["id"]
    device_name = device_info["name"]
    device_ip = device_info["ip"]
    device_type = device_info["type"]
    
    is_online, latency_ms = ping_device(device_ip)
    now = datetime.now(timezone.utc)

    entry = db.query(CameraHealth).filter(CameraHealth.id == device_id).first()
    
    # Get the last check time before updating
    last_check_time = entry.checked if entry else None
    if last_check_time and last_check_time.tzinfo is None:
        last_check_time = last_check_time.replace(tzinfo=timezone.utc)

    if not entry:
        entry_data = {
            "id": device_id, "status": "Unknown", "status_changed_at": now,
            "checked": now, "type": device_type
        }
        if device_type == 'Camera':
            entry_data["camera_id"] = device_id
        else: # NVR
            entry_data["nvr_id"] = device_id
        entry = CameraHealth(**entry_data)
        db.add(entry)
    
    current_status = entry.status
    new_status = "High Latency" if is_online and latency_ms > 50 else "Online" if is_online else "Offline"

    # Accurate uptime/downtime calculation logic based on delta
    if device_type == "Camera" and last_check_time:
        delta_seconds = (now - last_check_time).total_seconds()
        if delta_seconds > 0:
            today = now.date()
            daily_stat = db.query(CameraDailyStats).filter(
                CameraDailyStats.camera_id == device_id,
                CameraDailyStats.date == today
            ).first()

            if not daily_stat:
                daily_stat = CameraDailyStats(
                    camera_id=device_id, camera_name=device_name, date=today,
                    total_uptime_seconds=0, total_downtime_seconds=0
                )
                db.add(daily_stat)
            
            if new_status in ["Online", "High Latency"]:
                daily_stat.total_uptime_seconds += int(delta_seconds)
            else:
                daily_stat.total_downtime_seconds += int(delta_seconds)

            total_tracked = daily_stat.total_uptime_seconds + daily_stat.total_downtime_seconds
            daily_stat.uptime_percentage = (daily_stat.total_uptime_seconds / total_tracked) * 100 if total_tracked > 0 else 0

    # Status update logic
    if current_status != new_status:
        logger.info("Status change: %s from %s to %s.", device_name, current_status, new_status)
        entry.status_changed_at = now
        if current_status == "Offline" and new_status in ["Online", "High Latency"]:
            entry.last_online = now

    # Update health record with latest data
    entry.status = new_status
    entry.latency = latency_ms
    entry.checked = now
    entry.type = device_type

    return entry.status, entry.latency

# ==============================================================================
# MAIN FUNCTIONS (CALLED FROM OUTSIDE)
# ==============================================================================

def ping_all_devices():
    """Performs health checks on all active Cameras and NVRs."""
    process_start_time = time.perf_counter()
    db = SessionLocal()
    status_tracker = None
    try:
        status_tracker = db.query(HealthCheckStatus).get(1)
        if not status_tracker:
            status_tracker = HealthCheckStatus(id=1)
            db.add(status_tracker)
            db.commit()

        cameras = db.query(DBCamera).filter(and_(DBCamera.status != "Deactivated", DBCamera.status != "Standalone")).all()
        nvrs = db.query(NVR).filter(NVR.status != "Deactivated").all()
        devices = [{"id": cam.id, "name": cam.hostname, "ip": cam.ip, "type": "Camera"} for cam in cameras] + \
                  [{"id": nvr.id, "name": nvr.hostname, "ip": nvr.ip, "type": "NVR"} for nvr in nvrs]

        status_tracker.is_running = True
        status_tracker.start_time = datetime.now(timezone.utc)
        status_tracker.total_cameras = len(devices)
        status_tracker.completed_cameras = 0
        db.commit()

        logger.info("\ud83d\udd0d Starting health check for %d devices...", len(devices))

        for i, device_info in enumerate(devices, 1):
            # Call the integrated function for each device
            _perform_and_update_health_check(db, device_info)
            status_tracker.completed_cameras = i
            db.commit()

        logger.info("\u2705 Health check for all devices completed.")
        status_tracker.is_running = False
        status_tracker.start_time = None
        db.commit()

    except Exception as e:
        logger.critical("\u274c Critical error in ping_all_devices: %s", e, exc_info=True)
        if status_tracker:
            status_tracker.is_running = False
            db.commit()
    finally:
        db.close()
        duration = time.perf_counter() - process_start_time
        logger.info("\u23f1 Finished in %.2f seconds.", duration)


def ping_camera_by_id(camera_id: str):
    """Trigger a health check for a single camera by its ID."""
    db = SessionLocal()
    try:
        camera = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
        if not camera:
            logger.warning("Camera with ID %%{camera_id}%% not found.")
            return "Not Found", None
        
        device_info = {"id": camera.id, "name": camera.hostname, "ip": camera.ip, "type": "Camera"}
        status, latency = _perform_and_update_health_check(db, device_info)
        db.commit()
        return status, latency
        
    except Exception as e:
        logger.error("Ping error for camera ID %s: %s", camera_id, e, exc_info=True)
        db.rollback()
        return "Offline", None
    finally:
        db.close()


def ping_nvr_by_id(nvr_id: str):
    """Trigger a health check for a single NVR by its ID."""
    db = SessionLocal()
    try:
        nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
        if not nvr:
            logger.warning("NVR with ID %s not found.", nvr_id)
            return "Not Found", None

        # NVR does not have downtime statistics, but still use the same function for status consistency
        device_info = {"id": nvr.id, "name": nvr.hostname, "ip": nvr.ip, "type": "NVR"}
        status, latency = _perform_and_update_health_check(db, device_info)
        db.commit()
        return status, latency

    except Exception as e:
        logger.error("Ping error for NVR ID %s: %s", nvr_id, e, exc_info=True)
        db.rollback()
        return "Offline", None
    finally:
        db.close()

# Functions to be called by background tasks
def run_healthcheck_for_all():
    ping_all_devices()

def run_healthcheck_for_camera(camera_id):
    ping_camera_by_id(camera_id)

def run_healthcheck_for_nvr(nvr_id):
    ping_nvr_by_id(nvr_id)