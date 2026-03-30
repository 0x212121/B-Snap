import os
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO
from PIL import Image
import numpy as np
from sqlalchemy.orm import Session
from uuid import uuid4
from typing import Tuple, List

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
ALERT_COOLDOWN_MINUTES = 15     # Minimum 15 menit antara alert untuk kamera yang sama


def get_image_resolution(image_bytes: bytes) -> str:
    with Image.open(BytesIO(image_bytes)) as img:
        width, height = img.size
    return f"{width}x{height}"


def to_native_float(val):
    if isinstance(val, (np.float32, np.float64)):
        return float(val)
    return val


def is_alert_in_cooldown(health: CameraHealth, alert_reason: str) -> bool:
    """
    Check if alert is in cooldown period to prevent flooding.
    
    Returns True if alert should be suppressed, False otherwise.
    """
    now = datetime.now(timezone.utc)
    camera_name = health.camera.hostname if health.camera else "unknown"
    
    # Log status untuk debugging
    logger.debug(
        "[COOLDOWN CHECK] Camera: %s, Reason: %s, CooldownUntil: %s, LastSent: %s, LastReason: %s",
        camera_name,
        alert_reason,
        health.alert_cooldown_until.isoformat() if health.alert_cooldown_until else "None",
        health.last_email_sent.isoformat() if health.last_email_sent else "None",
        health.last_alert_reason
    )
    
    # Check explicit cooldown timestamp
    if health.alert_cooldown_until and health.alert_cooldown_until > now:
        remaining = (health.alert_cooldown_until - now).total_seconds()
        logger.info(
            "[COOLDOWN ACTIVE] Alert for %s suppressed for %.0f more seconds (until %s)",
            camera_name,
            remaining,
            health.alert_cooldown_until.isoformat()
        )
        return True
    
    # Check if same reason was alerted recently
    if (health.last_email_sent and 
        health.last_alert_reason == alert_reason and
        health.last_email_sent > now - timedelta(minutes=ALERT_COOLDOWN_MINUTES)):
        minutes_ago = (now - health.last_email_sent).total_seconds() / 60
        logger.info(
            "[DEDUPLICATION] Skipping %s alert for %s - same reason sent %.1f minutes ago",
            alert_reason,
            camera_name,
            minutes_ago
        )
        return True
    
    logger.debug("[COOLDOWN CHECK] Alert allowed for %s (%s)", camera_name, alert_reason)
    return False


def set_alert_cooldown(health: CameraHealth, reason: str):
    """Set cooldown period after sending alert."""
    now = datetime.now(timezone.utc)
    health.alert_cooldown_until = now + timedelta(minutes=ALERT_COOLDOWN_MINUTES)
    health.last_email_sent = now
    health.last_alert_reason = reason
    logger.info(
        "[COOLDOWN] Set %s cooldown for %s until %s",
        reason,
        health.camera.hostname if health.camera else "unknown",
        health.alert_cooldown_until.isoformat()
    )


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

    # === P0-001: Calculate file hash for integrity verification ===
    import hashlib
    file_hash = hashlib.sha256(image_bytes).hexdigest()

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
        file_hash=file_hash,  # P0-001: Store file hash
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

    # DEBUG: Log status transition for troubleshooting
    logger.info(
        "[TAMPER_DEBUG] %s: prev=%s, new=%s, consecutive_tamper=%d, is_tampered=%s",
        camera.hostname,
        prev_status,
        new_status,
        health.consecutive_tamper,
        is_tampered
    )

    # === Transisi: normal → tampered ===
    if prev_status != "tampered" and new_status == "tampered":
        logger.info(
            "[TAMPER_TRANSITION] %s: normal→tampered (consecutive=%d, threshold=%d)",
            camera.hostname,
            health.consecutive_tamper,
            TAMPER_CONFIRM_THRESHOLD
        )
        # Check cooldown to prevent flooding
        if is_alert_in_cooldown(health, snapshot.tamper_reason):
            logger.info(
                "[ALERT SKIP] %s is tampered but in cooldown period (%s)",
                camera.hostname,
                snapshot.tamper_reason
            )
        else:
            try:
                # BUG FIX: Pass snapshot.timestamp as incident_time (not current time)
                # This ensures email shows the ACTUAL time when tamper was detected
                logger.info(
                    "[ALERT_SEND] Calling send_tamper_alert for %s (%s)",
                    camera.hostname,
                    snapshot.tamper_reason
                )
                send_tamper_alert(
                    db, 
                    camera, 
                    snapshot.tamper_reason, 
                    abs_file_path,
                    incident_time=snapshot.timestamp  # Actual detection time from snapshot
                )
                # Note: Cooldown is now set INSIDE send_tamper_alert after dedup check
                # We don't need to set it again here
                logger.warning(
                    "[ALERT] %s marked tampered (%s) at %s",
                    camera.hostname,
                    snapshot.tamper_reason,
                    snapshot.timestamp.isoformat()
                )
            except Exception as e:
                # Even on failure, set short cooldown to prevent immediate retry flooding
                set_alert_cooldown(health, snapshot.tamper_reason)
                logger.exception("[ALERT_FAIL] Tamper email failed for %s: %s", camera.hostname, e)
                
                # Pastikan tidak duplikat di queue
                existing_retry = db.query(EmailRetryQueue).filter(
                    EmailRetryQueue.camera_id == camera.id,
                    EmailRetryQueue.type == "tamper",
                    EmailRetryQueue.sent == False
                ).first()
                if not existing_retry:
                    queue_email_retry(db, camera, "tamper", reason=snapshot.tamper_reason, file_path=abs_file_path, delay_minutes=5)
                else:
                    logger.info("[QUEUE] Skip duplicate tamper retry for %s", camera.hostname)

    # === Transisi: tampered → normal ===
    elif prev_status == "tampered" and new_status == "normal":
        # Recovery alerts are important - allow them even in cooldown
        # but use shorter cooldown to prevent flooding
        try:
            send_recovery_alert(db, camera)
            set_alert_cooldown(health, "recovery")
            logger.info("[RECOVERY] %s back to normal", camera.hostname)
        except Exception as e:
            # Set short cooldown even on failure
            health.alert_cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=5)
            logger.exception("[RECOVERY FAIL] Recovery email failed for %s: %s", camera.hostname, e)
            
            existing_retry = db.query(EmailRetryQueue).filter(
                EmailRetryQueue.camera_id == camera.id,
                EmailRetryQueue.type == "recovery",
                EmailRetryQueue.sent == False
            ).first()
            if not existing_retry:
                queue_email_retry(db, camera, "recovery", delay_minutes=5)
            else:
                logger.info("[QUEUE] Skip duplicate recovery retry for %s", camera.hostname)

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


def check_orphaned_snapshots(db: Session) -> Tuple[int, int]:
    """Check and mark orphaned snapshots (snapshots with camera_id that no longer exists).
    
    This function queries all snapshots where camera_id is not null and checks
    if the referenced camera still exists in the database. If not, the snapshot
    is marked as orphaned (is_orphaned = True).
    
    Args:
        db: Database session
        
    Returns:
        Tuple of (newly_orphaned_count, total_orphaned_count)
    """
    # Get all active camera IDs
    active_camera_ids = {cam.id for cam in db.query(Camera.id).all()}
    
    # Find snapshots that need to be checked
    # We check snapshots where:
    # 1. camera_id is not null (has a camera reference)
    # 2. Either is_orphaned is False or camera_id not in active cameras
    snapshots = db.query(Snapshot).filter(
        Snapshot.camera_id.isnot(None)
    ).all()
    
    newly_orphaned = 0
    already_orphaned = 0
    
    for snapshot in snapshots:
        if snapshot.camera_id not in active_camera_ids:
            if not snapshot.is_orphaned:
                snapshot.is_orphaned = True
                newly_orphaned += 1
                logger.info("[ORPHANED] Snapshot %s marked as orphaned (camera_id: %s not found)", 
                           snapshot.id, snapshot.camera_id)
            else:
                already_orphaned += 1
        else:
            # Camera exists, ensure is_orphaned is False
            if snapshot.is_orphaned:
                snapshot.is_orphaned = False
                logger.info("[RESTORED] Snapshot %s marked as not orphaned (camera_id: %s found)", 
                           snapshot.id, snapshot.camera_id)
    
    if newly_orphaned > 0:
        db.commit()
        logger.info("[ORPHAN CHECK] Marked %d snapshots as orphaned", newly_orphaned)
    
    total_orphaned = db.query(Snapshot).filter(Snapshot.is_orphaned == True).count()
    
    return newly_orphaned, total_orphaned


def get_orphaned_snapshots(db: Session, limit: int = None) -> List[Snapshot]:
    """Get all orphaned snapshots.
    
    Args:
        db: Database session
        limit: Optional limit on number of results
        
    Returns:
        List of orphaned Snapshot objects
    """
    query = db.query(Snapshot).filter(Snapshot.is_orphaned == True).order_by(Snapshot.timestamp.desc())
    
    if limit:
        query = query.limit(limit)
    
    return query.all()
