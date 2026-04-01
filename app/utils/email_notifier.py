import logging
from datetime import datetime, timezone, timedelta
import os
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# ambil semua dari email_helper (sudah include get_recipients_for_camera)
from app.models.email_retry_queue import EmailRetryQueue
from app.utils.email_helper import (
    _send_email_with_image,
    build_email_body,
    get_recipients_for_camera,
)
from app.models.camera_email_notification_log import (
    CameraEmailNotificationLog,
    CameraEmailNotificationRecipient,
)
from app.utils.timezone_helper import format_datetime_with_tz, to_current_timezone
from app.models.snapshot import Snapshot
from app.core.logging_config import set_debug_mode
from app.models.camera import Camera
from app.utils.email_template_renderer import render_template

set_debug_mode(False)

logger = logging.getLogger("email_notifier")
SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")  # absolute base dir

# Circuit Breaker Configuration
MAX_NOTIFICATION_FAILURES = 3  # Max failures before suppression
NOTIFICATION_SUPPRESS_MINUTES = 60  # Suppression duration after max failures
TAMPER_DEDUPLICATION_MINUTES = 15  # Minimum time between tamper alerts for same camera


def cleanup_old_email_logs(db: Session, days: int = None) -> int:
    from app.core.config import get_config
    if days is None:
        days = int(get_config("retention_email_logs_days", 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_logs = db.query(CameraEmailNotificationLog).filter(
        CameraEmailNotificationLog.sent_at < cutoff
    )
    deleted = old_logs.delete(synchronize_session=False)
    db.commit()

    if deleted:
        logger.info("Deleted %s old email logs older than %s days", deleted, days)

    return deleted


def is_smtp_configured() -> bool:
    """Check if SMTP is properly configured."""
    from app.utils.smtp_config import get_smtp_config
    config = get_smtp_config()
    return config.get("is_configured", False)


def is_notification_suppressed(db: Session, camera: Camera) -> bool:
    """
    Check if notification is suppressed for this camera.
    
    Circuit breaker pattern: After MAX_NOTIFICATION_FAILURES consecutive failures,
    notifications are suppressed for NOTIFICATION_SUPPRESS_MINUTES.
    
    Args:
        db: Database session
        camera: Camera object
        
    Returns:
        True if notification should be suppressed, False otherwise
    """
    # Check if explicitly suppressed
    if camera.notification_suppressed_until:
        if camera.notification_suppressed_until > datetime.now(timezone.utc):
            logger.info(
                "Notification suppressed for camera %s until %s (%d consecutive failures)",
                camera.hostname,
                camera.notification_suppressed_until.isoformat(),
                camera.notification_fail_count
            )
            return True
        else:
            # Suppression period ended, but we keep the failure count
            # It will be reset on successful send or camera recovery
            pass
    
    return False


def record_notification_failure(db: Session, camera: Camera, error_message: str):
    """
    Record a notification failure and activate suppression if needed.
    
    Circuit breaker: After MAX_NOTIFICATION_FAILURES consecutive failures,
    notifications are suppressed for NOTIFICATION_SUPPRESS_MINUTES.
    """
    camera.notification_fail_count += 1
    camera.last_notification_at = datetime.now(timezone.utc)
    
    # If we've reached max failures, activate suppression
    if camera.notification_fail_count >= MAX_NOTIFICATION_FAILURES:
        camera.notification_suppressed_until = datetime.now(timezone.utc) + timedelta(
            minutes=NOTIFICATION_SUPPRESS_MINUTES
        )
        logger.warning(
            "Camera %s has reached %d consecutive notification failures. "
            "Notifications suppressed for %d minutes.",
            camera.hostname,
            camera.notification_fail_count,
            NOTIFICATION_SUPPRESS_MINUTES
        )
    else:
        logger.info(
            "Notification failure %d/%d for camera %s: %s",
            camera.notification_fail_count,
            MAX_NOTIFICATION_FAILURES,
            camera.hostname,
            error_message
        )
    
    db.commit()


def record_notification_success(db: Session, camera: Camera):
    """
    Record a successful notification and reset failure counter.
    """
    camera.notification_fail_count = 0
    camera.notification_suppressed_until = None
    camera.last_notification_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Notification success for camera %s, failure counter reset", camera.hostname)


def reset_notification_suppression(db: Session, camera_id: str):
    """
    Reset notification suppression for a camera.
    Called when camera recovers (comes back online).
    
    Args:
        db: Database session
        camera_id: Camera ID
    """
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if camera and (camera.notification_fail_count > 0 or camera.notification_suppressed_until):
        camera.notification_fail_count = 0
        camera.notification_suppressed_until = None
        # Keep last_notification_at for deduplication purposes
        db.commit()
        logger.info(
            "Notification suppression reset for camera %s (camera recovered)",
            camera.hostname
        )


def should_send_tamper_alert(db: Session, camera: Camera, reason: str) -> bool:
    """
    Check if we should send a tamper alert (deduplication logic).
    
    Args:
        db: Database session
        camera: Camera object
        reason: Tamper reason
        
    Returns:
        True if alert should be sent, False otherwise
    """
    from app.models.health import CameraHealth
    
    # Check suppression first
    if is_notification_suppressed(db, camera):
        return False
    
    # Check CameraHealth cooldown (primary defense against flooding)
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if health:
        now = datetime.now(timezone.utc)
        
        # Check explicit cooldown timestamp
        if health.alert_cooldown_until and health.alert_cooldown_until > now:
            logger.info(
                "[DEDUP] Skipping tamper alert for %s - in cooldown until %s",
                camera.hostname,
                health.alert_cooldown_until.isoformat()
            )
            return False
        
        # Check if same reason was alerted recently
        if (health.last_email_sent and 
            health.last_alert_reason == reason and
            health.last_email_sent > now - timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)):
            minutes_ago = (now - health.last_email_sent).total_seconds() / 60
            logger.info(
                "[DEDUP] Skipping %s alert for %s - last alert was %.1f min ago",
                reason,
                camera.hostname,
                minutes_ago
            )
            return False
    
    # Check for recent tamper alerts in log (secondary defense)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)
    recent_alert = db.query(CameraEmailNotificationLog).filter(
        CameraEmailNotificationLog.camera_id == camera.id,
        CameraEmailNotificationLog.reason == reason,
        CameraEmailNotificationLog.sent_at >= cutoff
    ).first()
    
    if recent_alert:
        logger.info(
            "[DEDUP] Skipping tamper alert for camera %s (reason: %s) - "
            "recent alert exists from %s",
            camera.hostname,
            reason,
            recent_alert.sent_at.isoformat()
        )
        return False
    
    return True


def send_offline_incident_email_once(
    db: Session,
    *,
    camera,
    incident_started_at: datetime,
    offline_duration_seconds: int
) -> bool:
    
    # === CIRCUIT BREAKER: Check SMTP configuration first ===
    if not is_smtp_configured():
        logger.warning(
            "SMTP not configured. Skipping offline email for camera %s. "
            "Configure SMTP in /config page to enable notifications.",
            camera.hostname
        )
        return False
    
    # === CIRCUIT BREAKER: Check if notification is suppressed ===
    if is_notification_suppressed(db, camera):
        logger.info(
            "Offline notification suppressed for camera %s due to previous failures",
            camera.hostname
        )
        return False
    
    # 🔽 Ambil daftar penerima untuk camera ini (by group + by location)
    emails = get_recipients_for_camera(db, camera)

    if not emails:
        logger.warning(
            "No recipients found for camera %s (%s) in group %s location %s",
            camera.hostname,
            camera.ip,
            camera.group.name if camera.group else "No Group",
            camera.location,
        )
        return False

    # siapkan data email
    camera_group = camera.group.name if camera.group else "No Division"
    minutes = offline_duration_seconds // 60
    local_incident = format_datetime_with_tz(
        to_current_timezone(incident_started_at, db)
    )

    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.camera_id == camera.id)
        .order_by(Snapshot.timestamp.desc())
        .first()
    )

    snapshot_path = None
    snapshot_time = "N/A"
    if snapshot:
        snapshot_time = format_datetime_with_tz(
            to_current_timezone(snapshot.timestamp, db)
        )
        candidate_path = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
        if os.path.exists(candidate_path):
            snapshot_path = candidate_path
        else:
            logger.warning(
                "Snapshot file missing for camera %s (%s): %s",
                camera.hostname,
                camera.ip,
                candidate_path,
            )

    # cek apakah log sudah ada (idempotent per incident)
    existing_log = (
        db.query(CameraEmailNotificationLog)
        .filter(
            CameraEmailNotificationLog.camera_id == camera.id,
            CameraEmailNotificationLog.incident_started_at == incident_started_at,
        )
        .order_by(CameraEmailNotificationLog.id.desc())
        .first()
    )

    if existing_log:
        if existing_log.success:
            logger.info(
                "Email already sent for camera %s (incident %s). Skip re-sending.",
                camera.hostname,
                incident_started_at.isoformat(),
            )
            return False
        else:
            # Circuit breaker: Don't retry failed emails immediately
            # They will be handled by retry queue or wait for next incident
            logger.info(
                "Email log exists but failed for camera %s (incident %s). "
                "Skipping to prevent flooding.",
                camera.hostname,
                incident_started_at.isoformat(),
            )
            return False

    # buat log baru
    log = CameraEmailNotificationLog(
        camera_id=camera.id,
        camera_name=camera.hostname,
        incident_started_at=incident_started_at,
        sent_at=datetime.now(timezone.utc),
        success=False,
        error_message=None,
        reason="offline",
    )
    db.add(log)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.warning(
            "IntegrityError: Log already exists for camera %s (incident %s).",
            camera.hostname,
            incident_started_at.isoformat(),
        )
        return False

    # isi recipients sesuai daftar saat ini (immutable snapshot)
    for e in emails:
        db.add(CameraEmailNotificationRecipient(log_id=log.id, recipient_email=e))
    db.flush()

    # kirim email
    try:
        # Prepare template context
        template_context = {
            "camera_name": camera.hostname,
            "camera_ip": camera.ip,
            "camera_group": camera_group,
            "asset_no": camera.asset_no,
            "location": camera.location,
            "latitude": camera.latitude or "",
            "longitude": camera.longitude or "",
            "incident_time": local_incident,
            "offline_duration": str(minutes),
            "snapshot_time": snapshot_time if snapshot_time != "N/A" else None,
            "has_snapshot": bool(snapshot_path),
        }
        
        # Render template
        subject, plain_body, html_body = render_template("offline_alert", template_context, db)

        _send_email_with_image(
            to_emails=emails,
            subject=subject,
            cam_group=camera_group,
            cam_hostname=camera.hostname,
            snapshot_time=to_current_timezone(snapshot.timestamp, db) if snapshot else None,
            body=plain_body,
            html=html_body,
            image_path=snapshot_path,
        )

        log.success = True
        log.error_message = None
        log.sent_at = datetime.now(timezone.utc)
        db.commit()
        
        # Circuit breaker: Reset failure counter on success
        record_notification_success(db, camera)
        
        logger.info(
            "Successfully sent email alert for camera %s to %s",
            camera.hostname,
            emails,
        )
        return True
    except Exception as e:
        error_msg = str(e)
        log.success = False
        log.error_message = error_msg
        log.sent_at = datetime.now(timezone.utc)
        db.commit()

        # Circuit breaker: Record failure (may activate suppression)
        record_notification_failure(db, camera, error_msg)
        
        # Only queue retry if not SMTP config error (that won't fix itself in 5 min)
        if "SMTP not configured" not in error_msg:
            queue_email_retry(db, camera, "offline", reason="offline incident", delay_minutes=5)
        
        logger.exception("Failed to send offline email for %s: %s", camera.hostname, e)
        return False



def send_tamper_alert(db: Session, camera, reason: str, snapshot_path: str, incident_time: datetime = None):
    """
    Send tamper alert email.
    
    Args:
        db: Database session
        camera: Camera object
        reason: Tamper reason (blur, dark, etc.)
        snapshot_path: Path to snapshot image
        incident_time: ACTUAL time when tamper was detected (from snapshot timestamp)
                      If None, defaults to current time (but this is less accurate)
    """
    # Use provided incident_time or fall back to now (should always be provided)
    if incident_time is None:
        incident_time = datetime.now(timezone.utc)
        logger.warning(
            "[EMAIL_DEBUG] incident_time not provided for %s, using current time. "
            "This may cause inaccurate incident timestamps.",
            camera.hostname
        )
    
    logger.info(
        "[EMAIL_DEBUG] send_tamper_alert triggered for %s (%s) at %s",
        camera.hostname,
        reason,
        incident_time.isoformat()
    )
    
    # === CIRCUIT BREAKER: Check SMTP configuration first ===
    if not is_smtp_configured():
        logger.warning(
            "SMTP not configured. Skipping tamper alert for camera %s. "
            "Configure SMTP in /config page to enable notifications.",
            camera.hostname
        )
        return False
    
    # === CIRCUIT BREAKER: Check deduplication and suppression ===
    # MUST check this BEFORE setting cooldown, otherwise we'll always skip
    if not should_send_tamper_alert(db, camera, reason):
        return False
    
    # === IMMEDIATE COOLDOWN: Set cooldown AFTER dedup check ===
    # This prevents flooding even if send fails or function is called repeatedly
    from app.models.health import CameraHealth
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if health:
        # Set cooldown based on incident_time, not current time
        # This ensures cooldown is consistent with the actual incident
        cooldown_until = incident_time + timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)
        health.alert_cooldown_until = cooldown_until
        health.last_email_sent = incident_time
        health.last_alert_reason = reason
        db.commit()
        logger.info(
            "[COOLDOWN SET] %s cooldown set for %s until %s (based on incident time)",
            reason,
            camera.hostname,
            health.alert_cooldown_until.isoformat()
        )
    
    camera_group = camera.group.name if camera.group else "No Division"
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        logger.warning("No recipients found for %s", camera.hostname)
        return False

    # Format incident time for display (with timezone conversion)
    incident_time_str = format_datetime_with_tz(to_current_timezone(incident_time, db))
    
    # Prepare template context
    template_context = {
        "camera_name": camera.hostname,
        "camera_ip": camera.ip,
        "camera_group": camera_group,
        "reason": reason,
        "asset_no": camera.asset_no,
        "location": camera.location,
        "latitude": camera.latitude or "",
        "longitude": camera.longitude or "",
        "incident_time": incident_time_str,
        "has_snapshot": bool(snapshot_path),
    }
    
    # Render template
    subject, plain_body, html_body = render_template("tamper_alert", template_context, db)
    
    # Note: Cooldown was already set at the beginning of this function
    # using incident_time (snapshot timestamp), not current time
    
    try:
        _send_email_with_image(
            to_emails=recipients,
            subject=subject,
            cam_group=camera_group,
            cam_hostname=camera.hostname,
            snapshot_time=incident_time,  # BUG FIX: Add missing snapshot_time
            body=plain_body,
            html=html_body,
            image_path=snapshot_path,
        )
        
        # Create success log with ACTUAL incident time (from snapshot)
        # BUG FIX: Use incident_time instead of datetime.now()
        log = CameraEmailNotificationLog(
            camera_id=camera.id,
            camera_name=camera.hostname,
            incident_started_at=incident_time,  # Actual detection time
            sent_at=datetime.now(timezone.utc),  # Current time (when email sent)
            success=True,
            error_message=None,
            reason=reason,
        )
        db.add(log)
        db.flush()
        
        for r in recipients:
            db.add(CameraEmailNotificationRecipient(log_id=log.id, recipient_email=r))
        
        db.commit()
        
        # Circuit breaker: Reset failure counter on success
        record_notification_success(db, camera)
        
        logger.info("Tamper alert sent successfully for %s", camera.hostname)
        return True
        
    except Exception as e:
        error_msg = str(e)
        logger.exception("Failed to send tamper alert for %s: %s", camera.hostname, e)
        
        # Create failure log with ACTUAL incident time (from snapshot)
        # BUG FIX: Use incident_time instead of datetime.now()
        log = CameraEmailNotificationLog(
            camera_id=camera.id,
            camera_name=camera.hostname,
            incident_started_at=incident_time,  # Actual detection time
            sent_at=datetime.now(timezone.utc),  # Current time (when attempt made)
            success=False,
            error_message=error_msg,
            reason=reason,
        )
        db.add(log)
        db.flush()
        
        for r in recipients:
            db.add(CameraEmailNotificationRecipient(log_id=log.id, recipient_email=r))
        
        db.commit()
        
        # Circuit breaker: Record failure (may activate suppression)
        # Cooldown already set above, so this won't immediately retry
        record_notification_failure(db, camera, error_msg)
        
        return False


def send_recovery_alert(db: Session, camera, last_reason: str = None):
    """
    Send recovery notification when camera image returns to normal (from TAMPERED state).
    
    NOTE: This is DIFFERENT from send_online_alert which handles offline->online.
    - send_recovery_alert: Camera image was tampered (blur/dark/occluded), now clean
    - send_online_alert: Camera was offline (no ping response), now responding
    
    Also resets notification suppression (circuit breaker).
    """
    logger.info("[RECOVERY] Camera %s is back online", camera.hostname)
    
    # Reset suppression (camera has recovered)
    reset_notification_suppression(db, camera.id)
    
    # Check SMTP configuration
    if not is_smtp_configured():
        logger.debug("SMTP not configured, skipping recovery alert")
        return False
    
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        logger.warning("No recipients for recovery alert: %s", camera.hostname)
        return False
    
    camera_group = camera.group.name if camera.group else "No Division"
    
    # Get current time for recovery (with timezone conversion like snapshot)
    recovery_time = datetime.now(timezone.utc)
    recovery_time_str = format_datetime_with_tz(to_current_timezone(recovery_time, db))
    
    # Get latest snapshot for attachment (like in send_offline_incident_email_once)
    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.camera_id == camera.id)
        .order_by(Snapshot.timestamp.desc())
        .first()
    )
    
    snapshot_path = None
    snapshot_time = None
    snapshot_time_str = None
    if snapshot:
        snapshot_time = snapshot.timestamp
        snapshot_time_str = format_datetime_with_tz(to_current_timezone(snapshot.timestamp, db))
        candidate_path = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
        if os.path.exists(candidate_path):
            snapshot_path = candidate_path
        else:
            logger.warning(
                "Snapshot file missing for camera %s (%s): %s",
                camera.hostname,
                camera.ip,
                candidate_path,
            )
    
    # Prepare template context
    template_context = {
        "camera_name": camera.hostname,
        "camera_ip": camera.ip,
        "camera_group": camera_group,
        "last_reason": last_reason,
        "asset_no": camera.asset_no,
        "location": camera.location,
        "latitude": camera.latitude or "",
        "longitude": camera.longitude or "",
        "recovery_time": recovery_time_str,
        "snapshot_time": snapshot_time_str,
        "has_snapshot": bool(snapshot_path),
    }
    
    # Render template
    subject, plain_body, html_body = render_template("recovery_alert", template_context, db)
    
    try:
        _send_email_with_image(
            to_emails=recipients,
            subject=subject,
            cam_group=camera_group,
            cam_hostname=camera.hostname,
            snapshot_time=snapshot_time if snapshot_time else recovery_time,
            body=plain_body,
            html=html_body,
            image_path=snapshot_path,
        )
        
        # Log the recovery notification
        log = CameraEmailNotificationLog(
            camera_id=camera.id,
            camera_name=camera.hostname,
            incident_started_at=datetime.now(timezone.utc),
            sent_at=datetime.now(timezone.utc),
            success=True,
            error_message=None,
            reason="recovery",
        )
        db.add(log)
        db.flush()
        
        for r in recipients:
            db.add(CameraEmailNotificationRecipient(log_id=log.id, recipient_email=r))
        
        db.commit()
        logger.info("Recovery alert sent for %s", camera.hostname)
        return True
        
    except RuntimeError as e:
        # SMTP configuration/network errors - log as warning, not error
        logger.warning("Failed to send recovery alert: %s", e)
        return False
    except Exception as e:
        logger.exception("Failed to send recovery alert: %s", e)
        # Don't record failure for recovery - it's less critical
        return False


def queue_email_retry(db: Session, camera, alert_type: str, reason: str, delay_minutes: int = 5):
    """
    Queue an email for retry with exponential backoff.
    """
    scheduled_for = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    
    retry_entry = EmailRetryQueue(
        camera_id=camera.id,
        alert_type=alert_type,
        reason=reason,
        scheduled_for=scheduled_for,
        attempt_count=0,
    )
    db.add(retry_entry)
    db.commit()
    
    logger.info(
        "Queued %s retry for camera %s at %s",
        alert_type,
        camera.hostname,
        scheduled_for.isoformat()
    )


# NOTE: send_online_alert has been removed as per requirement.
# Online notifications (offline -> online) are intentionally disabled.
# Only the following notifications are sent:
# - send_tamper_alert: When camera image is tampered (blur/dark/occluded)
# - send_recovery_alert: When tampered camera image returns to normal
# - send_offline_incident_email_once: When camera goes offline
