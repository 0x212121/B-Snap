import logging
import os

from datetime import UTC, datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.logging_config import set_debug_mode
from app.models.camera import Camera
from app.models.camera_email_notification_log import (
    CameraEmailNotificationLog,
    CameraEmailNotificationRecipient,
)

# ambil semua dari email_helper (sudah include get_recipients_for_camera)
from app.models.email_retry_queue import EmailRetryQueue
from app.models.health import CameraHealth
from app.models.snapshot import Snapshot
from app.utils.email_delivery import EmailDeliveryResult, EmailTransportError, incident_utc
from app.utils.email_helper import (
    _send_email_with_image,
    build_email_body,
    get_recipients_for_camera,
)
from app.utils.email_retry_runtime import EmailRetryBudgetExpiredError
from app.utils.email_template_renderer import render_template
from app.utils.timezone_helper import format_datetime_with_tz, to_current_timezone

set_debug_mode(False)

logger = logging.getLogger("email_notifier")
SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")  # absolute base dir


def _camera_group_label(camera) -> str:
    groups = getattr(camera, "groups", None) or []
    if groups:
        return ", ".join(group.name for group in groups)
    legacy_group = getattr(camera, "group", None)
    return legacy_group.name if legacy_group else "No Group"

# Circuit Breaker Configuration
MAX_NOTIFICATION_FAILURES = 3  # Max failures before suppression
NOTIFICATION_SUPPRESS_MINUTES = 60  # Suppression duration after max failures
TAMPER_DEDUPLICATION_MINUTES = 15  # Minimum time between tamper alerts for same camera


# ============================================================================
# HELPER FUNCTIONS (NEW)
# ============================================================================

def _log_recipients_with_cc(
    db: Session, 
    log_id: int, 
    to_emails: List[str], 
    cc_email: Optional[str] = None
):
    """
    Robust helper untuk log recipients (To + CC).
    Handle: None, empty list, empty string, whitespace only, type safety.
    """
    # Log To recipients (filter None, empty, whitespace)
    if to_emails:
        for email in to_emails:
            if email and isinstance(email, str) and email.strip():
                recipient = CameraEmailNotificationRecipient(
                    log_id=log_id, 
                    recipient_email=email.strip()
                    # is_cc=False  # Uncomment jika field is_cc sudah ada di DB
                )
                db.add(recipient)
    
    # Log CC recipient jika valid
    if cc_email and isinstance(cc_email, str) and cc_email.strip():
        cc_recipient = CameraEmailNotificationRecipient(
            log_id=log_id, 
            recipient_email=cc_email.strip()
            # is_cc=True  # Uncomment jika field is_cc sudah ada di DB
        )
        db.add(cc_recipient)
        logger.debug(f"Logged CC recipient: {cc_email.strip()}")


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
    """
    # Check if explicitly suppressed
    if camera.notification_suppressed_until:
        if incident_utc(camera.notification_suppressed_until) > datetime.now(timezone.utc):
            logger.info(
                "Notification suppressed for camera %s until %s (%d consecutive failures)",
                camera.hostname,
                camera.notification_suppressed_until.isoformat(),
                camera.notification_fail_count,
            )
            return True
        else:
            # Suppression period ended, but we keep the failure count
            # It will be reset on successful send or camera recovery
            pass

    return False


def record_notification_failure(
    db: Session, camera: Camera, error_message: str, *, commit: bool = True
) -> None:
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
            NOTIFICATION_SUPPRESS_MINUTES,
        )
    else:
        logger.info(
            "Notification failure %d/%d for camera %s: %s",
            camera.notification_fail_count,
            MAX_NOTIFICATION_FAILURES,
            camera.hostname,
            error_message,
        )

    if commit:
        db.commit()


def record_notification_success(db: Session, camera: Camera, *, commit: bool = True) -> None:
    """
    Record a successful notification and reset failure counter.
    """
    camera.notification_fail_count = 0
    camera.notification_suppressed_until = None
    camera.last_notification_at = datetime.now(timezone.utc)
    if commit:
        db.commit()
    logger.info("Notification success for camera %s, failure counter reset", camera.hostname)


def reset_notification_suppression(db: Session, camera_id: str):
    """
    Reset notification suppression for a camera.
    Called when camera recovers (comes back online).
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
    """
    # Check suppression first
    if is_notification_suppressed(db, camera):
        return False

    # Check CameraHealth cooldown (primary defense against flooding)
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if health:
        now = datetime.now(timezone.utc)

        # Check explicit cooldown timestamp
        if health.alert_cooldown_until and incident_utc(health.alert_cooldown_until) > now:
            logger.info(
                "[DEDUP] Skipping tamper alert for %s - in cooldown until %s",
                camera.hostname,
                health.alert_cooldown_until.isoformat(),
            )
            return False

        # Check if same reason was alerted recently
        if (
            health.last_email_sent
            and health.last_alert_reason == reason
            and incident_utc(health.last_email_sent)
            > now - timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)
        ):
            minutes_ago = (now - incident_utc(health.last_email_sent)).total_seconds() / 60
            logger.info(
                "[DEDUP] Skipping %s alert for %s - last alert was %.1f min ago",
                reason,
                camera.hostname,
                minutes_ago,
            )
            return False

    # Check for recent tamper alerts in log (secondary defense)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)
    recent_alert = (
        db.query(CameraEmailNotificationLog)
        .filter(
            CameraEmailNotificationLog.camera_id == camera.id,
            CameraEmailNotificationLog.reason == reason,
            CameraEmailNotificationLog.success.is_(True),
            CameraEmailNotificationLog.sent_at >= cutoff,
        )
        .first()
    )

    if recent_alert:
        logger.info(
            "[DEDUP] Skipping tamper alert for camera %s (reason: %s) - "
            "recent alert exists from %s",
            camera.hostname,
            reason,
            recent_alert.sent_at.isoformat(),
        )
        return False

    return True


# ============================================================================
# EMAIL SEND FUNCTIONS (UPDATED)
# ============================================================================

def _notification_gate(
    db: Session,
    camera: Camera,
    incident_time: datetime,
    *,
    retry: bool,
    check_suppression: bool = True,
) -> EmailDeliveryResult | None:
    """Check incident identity and policy before attempting SMTP."""
    log = (
        db.query(CameraEmailNotificationLog)
        .filter_by(camera_id=camera.id, incident_started_at=incident_time)
        .first()
    )
    if log and log.success:
        return EmailDeliveryResult("already_sent", "incident_already_sent")
    if log and log.retry_exhausted:
        return EmailDeliveryResult("exhausted", "incident_retry_exhausted")
    if not is_smtp_configured():
        return EmailDeliveryResult("blocked", "smtp_not_configured")
    if check_suppression and is_notification_suppressed(db, camera):
        return EmailDeliveryResult(
            "deferred",
            "notification_suppressed",
            retry_at=incident_utc(camera.notification_suppressed_until),
        )
    if log and not retry:
        return EmailDeliveryResult(
            "deferred", "awaiting_retry", retry_at=datetime.now(UTC) + timedelta(minutes=5)
        )
    return None


def _deliver_notification(
    db: Session,
    camera: Camera,
    *,
    reason: str,
    incident_time: datetime,
    template: str,
    context: dict,
    recipients: list[str],
    snapshot_path: str | None,
    snapshot_time: datetime | None,
    count_failures: bool,
) -> EmailDeliveryResult:
    """Reuse an incident log and distinguish SMTP failures from policy decisions."""
    subject, plain_body, html_body = render_template(template, context, db)
    log = (
        db.query(CameraEmailNotificationLog)
        .filter_by(camera_id=camera.id, incident_started_at=incident_time)
        .first()
    )
    if log and log.success:
        return EmailDeliveryResult("already_sent", "incident_already_sent")
    if not log:
        log = CameraEmailNotificationLog(
            camera_id=camera.id,
            camera_name=camera.hostname,
            incident_started_at=incident_time,
            sent_at=datetime.now(UTC),
            success=False,
            reason=reason,
        )
        try:
            with db.begin_nested():
                db.add(log)
                db.flush()
        except IntegrityError:
            return EmailDeliveryResult(
                "deferred",
                "incident_in_progress",
                retry_at=datetime.now(UTC) + timedelta(minutes=5),
            )

    # SMTP errors are caught separately from database/template errors.
    # Only this call represents an actual delivery attempt.
    try:
        cc_used = _send_email_with_image(
            to_emails=recipients,
            subject=subject,
            cam_group=_camera_group_label(camera),
            cam_hostname=camera.hostname,
            snapshot_time=snapshot_time,
            body=plain_body,
            html=html_body,
            image_path=snapshot_path,
        )
    except EmailRetryBudgetExpiredError:
        return EmailDeliveryResult("deferred", "run_budget_exhausted")
    except EmailTransportError as exc:
        from app.utils.smtp_config import get_smtp_config

        log.success = False
        log.error_message = str(exc)
        log.sent_at = datetime.now(UTC)
        db.query(CameraEmailNotificationRecipient).filter_by(log_id=log.id).delete(
            synchronize_session=False
        )
        _log_recipients_with_cc(db, log.id, recipients, get_smtp_config().get("email_cc"))
        if count_failures:
            record_notification_failure(db, camera, str(exc), commit=False)
        db.commit()
        logger.warning("Email delivery failed for camera %s", camera.hostname)
        return EmailDeliveryResult("failed", "smtp_delivery_failed", smtp_attempted=True)

    log.success = True
    log.error_message = None
    log.sent_at = datetime.now(UTC)
    db.query(CameraEmailNotificationRecipient).filter_by(log_id=log.id).delete(
        synchronize_session=False
    )
    _log_recipients_with_cc(db, log.id, recipients, cc_used)
    if count_failures:
        record_notification_success(db, camera, commit=False)
    db.commit()
    return EmailDeliveryResult("sent", smtp_attempted=True)


def _latest_notification_snapshot(db: Session, camera: Camera) -> tuple:
    """Return the latest available snapshot attachment and its UTC timestamp."""
    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.camera_id == camera.id)
        .order_by(Snapshot.timestamp.desc())
        .first()
    )
    if not snapshot:
        return None, None
    candidate = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
    return (candidate if os.path.exists(candidate) else None), incident_utc(snapshot.timestamp)


def send_offline_incident_email_once(
    db: Session,
    *,
    camera: Camera,
    incident_started_at: datetime,
    offline_duration_seconds: int,
    retry: bool = False,
) -> EmailDeliveryResult:
    """Send or defer an offline incident without changing its identity on retry."""
    incident_started_at = incident_utc(incident_started_at)
    result = _notification_gate(db, camera, incident_started_at, retry=retry)
    if result is None:
        recipients = get_recipients_for_camera(db, camera)
        if not recipients:
            result = EmailDeliveryResult("blocked", "no_recipients")
        else:
            snapshot_path, snapshot_time = _latest_notification_snapshot(db, camera)
            context = {
                "camera_name": camera.hostname,
                "camera_ip": camera.ip,
                "camera_group": _camera_group_label(camera),
                "asset_no": camera.asset_no,
                "location": camera.location,
                "latitude": camera.latitude or "",
                "longitude": camera.longitude or "",
                "incident_time": format_datetime_with_tz(
                    to_current_timezone(incident_started_at, db)
                ),
                "offline_duration": str(offline_duration_seconds // 60),
                "snapshot_time": (
                    format_datetime_with_tz(to_current_timezone(snapshot_time, db))
                    if snapshot_time
                    else None
                ),
                "has_snapshot": bool(snapshot_path),
            }
            result = _deliver_notification(
                db,
                camera,
                reason="offline",
                incident_time=incident_started_at,
                template="offline_alert",
                context=context,
                recipients=recipients,
                snapshot_path=snapshot_path,
                snapshot_time=snapshot_time,
                count_failures=True,
            )
    if not retry and result.status in {"failed", "deferred", "blocked"}:
        queue_email_retry(
            db,
            camera,
            "offline",
            reason="offline incident",
            incident_time=incident_started_at,
            offline_duration_seconds=offline_duration_seconds,
            retry_at=result.retry_at,
        )
    return result


def send_tamper_alert(
    db: Session,
    camera: Camera,
    reason: str,
    snapshot_path: str | None,
    incident_time: datetime | None = None,
    *,
    retry: bool = False,
) -> EmailDeliveryResult:
    """Send tamper evidence with stable incident time and explicit deferrals."""
    incident_time = incident_utc(incident_time or datetime.now(UTC))
    result = _notification_gate(db, camera, incident_time, retry=retry)
    if result is not None:
        return result
    # Preserve deduplication before setting cooldown, including successful recent alerts.
    if not should_send_tamper_alert(db, camera, reason):
        health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
        retry_at = datetime.now(UTC) + timedelta(minutes=TAMPER_DEDUPLICATION_MINUTES)
        if health and health.alert_cooldown_until:
            retry_at = max(retry_at, incident_utc(health.alert_cooldown_until))
        return EmailDeliveryResult("deferred", "tamper_cooldown", retry_at=retry_at)
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        return EmailDeliveryResult("blocked", "no_recipients")
    health = db.query(CameraHealth).filter_by(camera_id=camera.id).first()
    if health:
        # The throttle follows delivery time; email evidence keeps the original incident time.
        health.alert_cooldown_until = datetime.now(UTC) + timedelta(
            minutes=TAMPER_DEDUPLICATION_MINUTES
        )
        health.last_email_sent = datetime.now(UTC)
        health.last_alert_reason = reason
        db.commit()
    context = {
        "camera_name": camera.hostname,
        "camera_ip": camera.ip,
        "camera_group": _camera_group_label(camera),
        "reason": reason,
        "asset_no": camera.asset_no,
        "location": camera.location,
        "latitude": camera.latitude or "",
        "longitude": camera.longitude or "",
        "incident_time": format_datetime_with_tz(to_current_timezone(incident_time, db)),
        "has_snapshot": bool(snapshot_path),
    }
    return _deliver_notification(
        db,
        camera,
        reason=reason,
        incident_time=incident_time,
        template="tamper_alert",
        context=context,
        recipients=recipients,
        snapshot_path=snapshot_path,
        snapshot_time=incident_time,
        count_failures=True,
    )


def send_recovery_alert(
    db: Session,
    camera: Camera,
    last_reason: str | None = None,
    *,
    incident_time: datetime | None = None,
    retry: bool = False,
) -> EmailDeliveryResult:
    """Keep the original recovery time and reset suppression only on the transition."""
    incident_time = incident_utc(incident_time or datetime.now(UTC))
    if not retry:
        reset_notification_suppression(db, camera.id)
    result = _notification_gate(db, camera, incident_time, retry=retry, check_suppression=False)
    if result is not None:
        return result
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        return EmailDeliveryResult("blocked", "no_recipients")
    snapshot_path, snapshot_time = _latest_notification_snapshot(db, camera)
    context = {
        "camera_name": camera.hostname,
        "camera_ip": camera.ip,
        "camera_group": _camera_group_label(camera),
        "last_reason": last_reason,
        "asset_no": camera.asset_no,
        "location": camera.location,
        "latitude": camera.latitude or "",
        "longitude": camera.longitude or "",
        "recovery_time": format_datetime_with_tz(to_current_timezone(incident_time, db)),
        "snapshot_time": (
            format_datetime_with_tz(to_current_timezone(snapshot_time, db))
            if snapshot_time
            else None
        ),
        "has_snapshot": bool(snapshot_path),
    }
    return _deliver_notification(
        db,
        camera,
        reason="recovery",
        incident_time=incident_time,
        template="recovery_alert",
        context=context,
        recipients=recipients,
        snapshot_path=snapshot_path,
        snapshot_time=snapshot_time or incident_time,
        count_failures=False,
    )


def queue_email_retry(
    db: Session,
    camera: Camera,
    alert_type: str,
    reason: str | None = None,
    delay_minutes: int = 5,
    *,
    file_path: str | None = None,
    incident_time: datetime | None = None,
    offline_duration_seconds: int | None = None,
    retry_at: datetime | None = None,
) -> EmailRetryQueue | None:
    """Enqueue once without reviving an exhausted task for the same incident."""
    incident_time = incident_utc(incident_time or datetime.now(UTC))
    existing = (
        db.query(EmailRetryQueue)
        .filter_by(
            camera_id=camera.id,
            type=alert_type,
            incident_time=incident_time,
        )
        .first()
    )
    if existing:
        return existing
    terminal_log = (
        db.query(CameraEmailNotificationLog)
        .filter_by(
            camera_id=camera.id,
            incident_started_at=incident_time,
            retry_exhausted=True,
        )
        .first()
    )
    if terminal_log:
        return None
    active = (
        db.query(EmailRetryQueue)
        .filter_by(
            camera_id=camera.id,
            type=alert_type,
            status="pending",
        )
        .first()
    )
    if active:
        return active
    scheduled_for = (
        incident_utc(retry_at) if retry_at else datetime.now(UTC) + timedelta(minutes=delay_minutes)
    )
    retry_entry = EmailRetryQueue(
        id=str(uuid4()),
        camera_id=camera.id,
        type=alert_type,
        reason=reason,
        file_path=file_path,
        incident_time=incident_time,
        offline_duration_seconds=offline_duration_seconds,
        next_retry_at=scheduled_for,
        attempts=0,
        status="pending",
        max_attempts=max(1, get_config("email_retry_max_attempts", 5)),
    )
    try:
        with db.begin_nested():
            db.add(retry_entry)
            db.flush()
    except IntegrityError:
        # A concurrent producer may have won the pending camera/type unique index.
        active = (
            db.query(EmailRetryQueue)
            .filter_by(
                camera_id=camera.id,
                type=alert_type,
                status="pending",
            )
            .first()
        )
        if active is None:
            raise
        return active
    db.commit()
    logger.info(
        "Queued %s retry for camera %s at %s",
        alert_type,
        camera.hostname,
        scheduled_for.isoformat(),
    )
    return retry_entry
