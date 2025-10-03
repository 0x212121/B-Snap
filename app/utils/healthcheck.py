from datetime import datetime, timezone
import time
from sqlalchemy import and_, desc
from sqlalchemy.orm import Session
from app.models.health import CameraHealth
from app.models.nvr import NVR
from app.models.camera import Camera as DBCamera
from app.models.health_check_status import HealthCheckStatus
from app.models.camera_daily_stats import CameraDailyStats
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.db.database import SessionLocal
from ping3 import ping, errors
from app.core.logging_config import setup_logging
import logging
from app.models.task_timing import TaskTiming
from app.utils.notify import cleanup_old_email_logs
from app.utils.notify import send_offline_incident_email_once

# setup logging
setup_logging()
logger = logging.getLogger("healthcheck")

PING_TIMEOUT = 1.0
PING_ATTEMPTS = 3


def ping_device(ip: str) -> tuple[bool, int | None]:
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


def log_status_change(db: Session, camera_id: str, prev_status: str, new_status: str, changed_at: datetime):
    if prev_status == new_status:
        return

    last_log = db.query(CameraStatusChangeLog) \
        .filter(CameraStatusChangeLog.camera_id == camera_id) \
        .order_by(CameraStatusChangeLog.changed_at.desc()) \
        .first()

    duration = None
    if last_log is not None:
        if last_log.changed_at.tzinfo is None:
            last_log_time = last_log.changed_at.replace(tzinfo=timezone.utc)
        else:
            last_log_time = last_log.changed_at
        duration = int((changed_at - last_log_time).total_seconds())

    new_log = CameraStatusChangeLog(
        camera_id=camera_id,
        previous_status=prev_status,
        new_status=new_status,
        changed_at=changed_at,
        duration_since_last_change=duration
    )
    db.add(new_log)


def _perform_and_update_health_check(db: Session, device_info: dict) -> tuple[str, int | None]:
    device_id = device_info["id"]
    device_name = device_info["name"]
    device_ip = device_info["ip"]
    device_type = device_info["type"]

    is_online, latency_ms = ping_device(device_ip)
    now = datetime.now(timezone.utc)

    entry = db.query(CameraHealth).filter(CameraHealth.id == device_id).first()

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
        else:
            entry_data["nvr_id"] = device_id
        entry = CameraHealth(**entry_data)
        db.add(entry)

    current_status = entry.status
    new_status = "High Latency" if is_online and latency_ms > 50 else "Online" if is_online else "Offline"

    # Update daily stats untuk Camera
    if device_type == "Camera" and last_check_time:
        delta_seconds = (now - last_check_time).total_seconds()
        delta_seconds = max(0, delta_seconds)

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

            if current_status == new_status:
                if new_status in ["Online", "High Latency"]:
                    daily_stat.total_uptime_seconds += int(delta_seconds)
                else:
                    daily_stat.total_downtime_seconds += int(delta_seconds)
            else:
                if current_status in ["Online", "High Latency"]:
                    daily_stat.total_uptime_seconds += int(delta_seconds)
                else:
                    daily_stat.total_downtime_seconds += int(delta_seconds)

            total_tracked = daily_stat.total_uptime_seconds + daily_stat.total_downtime_seconds
            daily_stat.uptime_percentage = (
                (daily_stat.total_uptime_seconds / total_tracked) * 100 if total_tracked > 0 else 0
            )

            if total_tracked > 86400:
                excess = total_tracked - 86400
                if daily_stat.total_downtime_seconds >= excess:
                    daily_stat.total_downtime_seconds -= excess
                else:
                    remainder = excess - daily_stat.total_downtime_seconds
                    daily_stat.total_downtime_seconds = 0
                    daily_stat.total_uptime_seconds = max(0, daily_stat.total_uptime_seconds - remainder)

                logger.warning("⚠️ %s Auto-fixed uptime overflow. Trimmed %d seconds to fit 86400s.",
                               device_name, excess)
                total_tracked = daily_stat.total_uptime_seconds + daily_stat.total_downtime_seconds
                daily_stat.uptime_percentage = (
                    (daily_stat.total_uptime_seconds / total_tracked) * 100 if total_tracked > 0 else 0
                )

    # Status change
    if current_status != new_status:
        logger.info("Status change: %s from %s to %s.", device_name, current_status, new_status)

        if device_type == "Camera":
            log_status_change(db, device_id, current_status, new_status, now)
            db.flush()  # <--- penting, pastikan log tersimpan ke DB session

        entry.status_changed_at = now
        if current_status == "Offline" and new_status in ["Online", "High Latency"]:
            entry.last_online = now

    # Update health entry
    entry.status = new_status
    entry.latency = latency_ms
    entry.checked = now
    entry.type = device_type

    try:
        if device_type == "Camera" and entry.status == "Offline":
            last_offline_log = db.query(CameraStatusChangeLog) \
                .filter(
                    CameraStatusChangeLog.camera_id == device_id,
                    CameraStatusChangeLog.new_status == "Offline"
                ) \
                .order_by(desc(CameraStatusChangeLog.changed_at)) \
                .first()

            if last_offline_log:
                incident_started_at = last_offline_log.changed_at
                if incident_started_at.tzinfo is None:
                    incident_started_at = incident_started_at.replace(tzinfo=timezone.utc)

                offline_duration_seconds = int((now - incident_started_at).total_seconds())
                if offline_duration_seconds >= 1800:  # 30 minutes
                    camera_obj = db.query(DBCamera).filter(DBCamera.id == device_id).first()
                    if camera_obj:
                        sent = send_offline_incident_email_once(
                            db,
                            camera=camera_obj,
                            incident_started_at=incident_started_at,
                            offline_duration_seconds=offline_duration_seconds
                        )
                        if sent:
                            logger.info("📨 Email offline-30m sent once for %s (since %s).",
                                        camera_obj.hostname, incident_started_at)
    except Exception as notif_err:
        logger.warning("Notify offline-30m failed for device %s: %s", device_id, notif_err, exc_info=True)

    return entry.status, entry.latency


def ping_all_devices():
    time_start = time.monotonic()
    started_at = datetime.now(timezone.utc)

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

        logger.info("🔍 Starting health check for %d devices...", len(devices))

        for i, device_info in enumerate(devices, 1):
            _perform_and_update_health_check(db, device_info)

            status_tracker = db.query(HealthCheckStatus).get(1)
            if status_tracker:
                status_tracker.completed_cameras = i

            if i % 10 == 0:
                db.commit()
                db.expunge_all()

        # commit terakhir untuk sisa device (<10 atau bukan kelipatan 10)
        db.commit()

        logger.info("✅ Health check for all devices completed.")
        status_tracker = db.query(HealthCheckStatus).get(1)
        if status_tracker:
            status_tracker.is_running = False
            status_tracker.start_time = None
            db.commit()

        status = "success"
        try:
            deleted = cleanup_old_email_logs(db, days=90)
            if deleted:
                logger.info("🧹 Cleaned up %d email notification logs older than 90 days.", deleted)
        except Exception as e:
            logger.warning("Failed to cleanup old email logs: %s", e)
    except Exception as e:
        logger.critical("❌ Critical error in ping_all_devices: %s", e, exc_info=True)
        if status_tracker:
            status_tracker.is_running = False
            db.commit()
        status = "fail"
    finally:
        ended_at = datetime.now(timezone.utc)
        duration_ms = int((time.monotonic() - time_start) * 1000)

        try:
            db.add(TaskTiming(
                task_name='ping_all_devices',
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                status=status
            ))
            db.commit()
        except Exception as e:
            logger.warning("[TimingLog] Failed to log ping timing: %s", e)
        db.close()

    logger.info("⏱️ Finished in %.2f seconds.", duration_ms / 1000)


def ping_camera_by_id(camera_id: str):
    db = SessionLocal()
    try:
        camera = db.query(DBCamera).filter(DBCamera.id == camera_id).first()
        if not camera:
            logger.warning("Camera with ID %s not found.", camera_id)
            return "Not Found", None

        device_info = {"id": camera.id, "name": camera.hostname, "ip": camera.ip, "type": "Camera"}
        status, latency = _perform_and_update_health_check(db, device_info)
        db.flush()   # <--- flush sebelum commit
        db.commit()
        return status, latency

    except Exception as e:
        logger.error("Ping error for camera ID %s: %s", camera_id, e, exc_info=True)
        db.rollback()
        return "Offline", None
    finally:
        db.close()


def ping_nvr_by_id(nvr_id: str):
    db = SessionLocal()
    try:
        nvr = db.query(NVR).filter(NVR.id == nvr_id).first()
        if not nvr:
            logger.warning("NVR with ID %s not found.", nvr_id)
            return "Not Found", None

        device_info = {"id": nvr.id, "name": nvr.hostname, "ip": nvr.ip, "type": "NVR"}
        status, latency = _perform_and_update_health_check(db, device_info)
        db.flush()   # <--- flush sebelum commit
        db.commit()
        return status, latency

    except Exception as e:
        logger.error("Ping error for NVR ID %s: %s", nvr_id, e, exc_info=True)
        db.rollback()
        return "Offline", None
    finally:
        db.close()


# Background task wrappers
def run_healthcheck_for_all():
    ping_all_devices()


def run_healthcheck_for_camera(camera_id):
    ping_camera_by_id(camera_id)


def run_healthcheck_for_nvr(nvr_id):
    ping_nvr_by_id(nvr_id)
