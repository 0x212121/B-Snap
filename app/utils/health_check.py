from datetime import datetime, timezone, date, timedelta
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

# Global variable for tracking status
healthcheck_status = {
    "is_running": False,
    "start_time": None,
    "total_cameras": 0,
    "completed_cameras": 0
}

PING_TIMEOUT = 1.0
PING_ATTEMPTS = 3

def update_camera_daily_stats(db: Session, camera_id: str, camera_name: str, status: str, start_time: datetime, end_time: datetime):
    """
    Calculates the duration of the previous status and updates the daily camera statistics.
    This function now handles both naive and aware datetimes to prevent TypeErrors.
    """
    if not start_time or not end_time:
        logger.warning(f"Invalid start or end time for camera_id: {camera_id}. Skipping calculation.")
        return

    # Ensure both datetimes are offset-aware before subtraction.
    # This handles legacy data in the DB that might be offset-naive.
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds <= 0:
        return  # Invalid or negative duration

    current_day = start_time.date()
    end_day = end_time.date()

    # Iterate for each day if the duration crosses midnight
    while current_day <= end_day:
        day_start_dt = datetime.combine(current_day, datetime.min.time(), tzinfo=timezone.utc)
        next_day_start_dt = datetime.combine(current_day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        # Determine the relevant time range for the current day
        effective_start = max(start_time, day_start_dt)
        effective_end = min(end_time, next_day_start_dt)

        daily_duration = (effective_end - effective_start).total_seconds()
        if daily_duration <= 0:
            current_day += timedelta(days=1)
            continue

        # Find or create the daily stats record
        daily_stat = db.query(CameraDailyStats).filter(
            CameraDailyStats.camera_id == camera_id,
            CameraDailyStats.date == current_day
        ).first()

        if not daily_stat:
            daily_stat = CameraDailyStats(
                camera_id=camera_id,
                camera_name=camera_name,
                date=current_day,
                total_uptime_seconds=0,
                total_downtime_seconds=0
            )
            db.add(daily_stat)
            db.flush()  # flush to ensure the object exists in the session before update

        # Update uptime or downtime duration
        if status in ["Online", "High Latency"]:
            daily_stat.total_uptime_seconds += int(daily_duration)
        elif status == "Offline":
            daily_stat.total_downtime_seconds += int(daily_duration)

        # Recalculate uptime percentage
        total_tracked_duration = daily_stat.total_uptime_seconds + daily_stat.total_downtime_seconds
        if total_tracked_duration > 0:
            daily_stat.uptime_percentage = (daily_stat.total_uptime_seconds / total_tracked_duration) * 100
        else:
            daily_stat.uptime_percentage = 0

        current_day += timedelta(days=1)


def ping_device(ip: str) -> tuple[bool, int | None]:
    """
    Pings a device multiple times and returns its status and average latency.
    A device is considered 'Online' if at least one ping attempt is successful.
    """
    latencies = []
    try:
        for _ in range(PING_ATTEMPTS):
            latency = ping(ip, timeout=PING_TIMEOUT, unit='s')
            
            # --- BUG FIX ---
            # The original check `isinstance(latency, (float, int))` was flawed because `isinstance(False, int)` is True.
            # This incorrectly treated a timeout (which returns False) as a successful ping with 0 latency.
            # The corrected check ensures that only successful pings (which return a float) are recorded.
            # Pings that result in False (timeout) or None (error) are now correctly ignored,
            # leading to an "Offline" status if no pings succeed.
            if isinstance(latency, float):
                latencies.append(latency)
        
        # If after all attempts there are no successful pings, the device is offline.
        if not latencies:
            return False, None
            
    except errors.DestinationHostUnreachable:
        # This error means the host is definitively unreachable.
        return False, None
    except Exception as e:
        logger.error(f"An unexpected error occurred while pinging {ip}: {e}")
        return False, None
    
    # If we get here, at least one ping was successful.
    avg_latency = sum(latencies) / len(latencies)
    # Calculate average latency in milliseconds and return online status.
    return True, int(avg_latency * 1000)


def ping_all_devices():
    """Performs a health check on all active Cameras and NVRs."""
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

        logger.info(f"🔍 Starting health check for {len(devices)} devices...")

        for i, device in enumerate(devices, 1):
            is_online, latency_ms = ping_device(device["ip"])
            now = datetime.now(timezone.utc)

            entry = db.query(CameraHealth).filter(CameraHealth.id == device["id"]).first()
            if not entry:
                entry_data = {
                    "id": device["id"],
                    "status": "Unknown",
                    "status_changed_at": now,
                    "checked": now,
                    "type": device["type"]
                }
                if device['type'] == 'Camera':
                    entry_data["camera_id"] = device["id"]
                else: # NVR
                    entry_data["nvr_id"] = device["id"]
                entry = CameraHealth(**entry_data)
                db.add(entry)
            
            current_status = entry.status
            last_change_time = entry.status_changed_at

            new_status = "Offline"
            if is_online:
                # Latency check for devices that are online
                new_status = "High Latency" if latency_ms > 50 else "Online"

            if current_status != new_status:
                logger.info(f"Status change: {device['name']} from {current_status} to {new_status}.")
                if current_status != "Unknown" and device['type'] == 'Camera':
                    update_camera_daily_stats(db, device["id"], device["name"], current_status, last_change_time, now)
                
                entry.status_changed_at = now
                if current_status == "Offline" and new_status in ["Online", "High Latency"]:
                    entry.last_online = now
            
            entry.status = new_status
            entry.latency = latency_ms
            entry.checked = now
            entry.type = device["type"]
            
            status_tracker.completed_cameras = i
            db.commit()

        logger.info("✅ Health check for all devices completed.")
        status_tracker.is_running = False
        status_tracker.start_time = None
        db.commit()

    except Exception as e:
        logger.critical(f"❌ Critical error in ping_all_devices: {e}", exc_info=True)
        if status_tracker:
            status_tracker.is_running = False
            db.commit()
    finally:
        db.close()
        duration = time.perf_counter() - process_start_time
        logger.info(f"⏱ Finished in {duration:.2f} seconds.")


def _update_single_device_health(db: Session, device, device_type: str):
    """Helper function to update health for a single device (Camera or NVR)."""
    
    is_online, latency_ms = ping_device(device.ip)
    now = datetime.now(timezone.utc)

    entry = db.query(CameraHealth).filter(CameraHealth.id == device.id).first()
    if not entry:
        entry_data = {
            "id": device.id,
            "status": "Unknown",
            "status_changed_at": now,
            "checked": now,
            "type": device_type
        }
        if device_type == 'Camera':
            entry_data["camera_id"] = device.id
        else: # NVR
            entry_data["nvr_id"] = device.id
        entry = CameraHealth(**entry_data)
        db.add(entry)
    
    current_status = entry.status
    last_change_time = entry.status_changed_at

    new_status = "Offline"
    if is_online:
        new_status = "High Latency" if latency_ms > 50 else "Online"

    if current_status != new_status:
        logger.info(f"Status change for single device: {device.hostname} from {current_status} to {new_status}.")
        if current_status != "Unknown" and device_type == 'Camera':
            update_camera_daily_stats(db, device.id, device.hostname, current_status, last_change_time, now)

        entry.status_changed_at = now
        if current_status == "Offline" and new_status in ["Online", "High Latency"]:
            entry.last_online = now

    entry.status = new_status
    entry.latency = latency_ms
    entry.checked = now
    entry.type = device_type
    
    db.commit()
    return entry.status, entry.latency

def ping_camera_by_id(camera_id: str):
    """Triggers a health check for a specific camera by its ID."""
    db = SessionLocal()
    try:
        camera = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
        if not camera:
            logger.warning(f"Camera with ID {camera_id} not found.")
            return "Not Found", None
        return _update_single_device_health(db, camera, "Camera")
    except Exception as e:
        logger.error(f"Ping error for camera ID {camera_id}: {e}", exc_info=True)
        return "Offline", None
    finally:
        db.close()

def ping_nvr_by_id(nvr_id: str):
    """Triggers a health check for a specific NVR by its ID."""
    db = SessionLocal()
    try:
        nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
        if not nvr:
            logger.warning(f"NVR with ID {nvr_id} not found.")
            return "Not Found", None
        return _update_single_device_health(db, nvr, "NVR")
    except Exception as e:
        logger.error(f"Ping error for NVR ID {nvr_id}: {e}", exc_info=True)
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

