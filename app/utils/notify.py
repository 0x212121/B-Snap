import logging
from datetime import datetime, timezone, timedelta
import os
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.utils.email_helper import _send_email_with_image
from app.models.recipient import GroupRecipient
from app.models.camera_email_notification_log import (
    CameraEmailNotificationLog,
    CameraEmailNotificationRecipient,
)
from app.utils.timezone_helper import format_datetime_with_tz, to_current_timezone
from app.models.snapshot import Snapshot

logger = logging.getLogger("email_notifier")
SNAPSHOT_BASE_DIR = os.path.join("static", "snapshots")  # absolute base dir


def cleanup_old_email_logs(db: Session, days: int = 90) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    old_logs = db.query(CameraEmailNotificationLog).filter(
        CameraEmailNotificationLog.sent_at < cutoff
    )
    deleted = old_logs.delete(synchronize_session=False)
    db.commit()

    if deleted:
        logger.info("Deleted %s old email logs older than %s days", deleted, days)

    return deleted


def send_offline_incident_email_once(
    db: Session,
    *,
    camera,
    incident_started_at: datetime,
    offline_duration_seconds: int
) -> bool:
    # ambil daftar penerima saat ini
    # ambil daftar penerima hanya untuk group kamera ini
    if camera.group:
        recipients = db.query(GroupRecipient).filter(
            GroupRecipient.group_id == camera.group.id
        ).all()
    else:
        recipients = []

    emails = [r.email for r in recipients if r.email]
    if not emails:
        logger.warning("No recipients found for camera %s (%s) in group %s",
                    camera.hostname, camera.ip,
                    camera.group.name if camera.group else "No Group")
        return False

    # siapkan konten email
    camera_group = camera.group.name if camera.group else "No Division"
    minutes = offline_duration_seconds // 60
    local_incident = format_datetime_with_tz(to_current_timezone(incident_started_at, db))

    snapshot = (
        db.query(Snapshot)
        .filter(Snapshot.camera_id == camera.id)
        .order_by(Snapshot.timestamp.desc())
        .first()
    )

    snapshot_path = None
    snapshot_time = "N/A"
    if snapshot:
        snapshot_time = format_datetime_with_tz(to_current_timezone(snapshot.timestamp, db))
        candidate_path = os.path.join(SNAPSHOT_BASE_DIR, snapshot.file_path)
        if os.path.exists(candidate_path):
            snapshot_path = candidate_path
        else:
            logger.warning("Snapshot file missing for camera %s (%s): %s",
                           camera.hostname, camera.ip, candidate_path)

    subject = f"🚨 [{camera_group}] CCTV {camera.hostname} OFFLINE > 30 minutes"
    body = (
        f"CCTV {camera.hostname} (IP: {camera.ip}) has been offline for more than {minutes} minutes.\n"
        f"Incident started: {local_incident}.\n"
        f"Last snapshot: {snapshot_time}.\n"
        "Please check immediately."
    )
    html = (
        f"<h2>[{camera_group}] 🚨 CCTV Alert</h2>"
        f"<p><b>{camera.hostname}</b> (IP: {camera.ip}) offline more than <b>{minutes} minutes</b>.</p>"
        f"<p>Incident started: <code>{local_incident}</code></p>"
    )
    if snapshot:
        html += f"<p>Last snapshot at {snapshot_time} :</p>"
    else:
        html += "<p><i>No snapshot available.</i></p>"

    # cek apakah log sudah ada (idempotent per incident)
    existing_log = db.query(CameraEmailNotificationLog).filter(
        CameraEmailNotificationLog.camera_id == camera.id,
        CameraEmailNotificationLog.incident_started_at == incident_started_at
    ).order_by(CameraEmailNotificationLog.id.desc()).first()

    if existing_log:
        if existing_log.success:
            logger.info(
                "Email already sent for camera %s (incident %s). Skip re-sending.",
                camera.hostname, incident_started_at.isoformat()
            )
            return False
        else:
            logger.info(
                "Email log already exists but marked failed for camera %s (incident %s). Skip re-sending.",
                camera.hostname, incident_started_at.isoformat()
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
    )
    db.add(log)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.warning(
            "IntegrityError: Log already exists for camera %s (incident %s).",
            camera.hostname, incident_started_at.isoformat()
        )
        return False

    # isi recipients sesuai daftar saat ini (immutable snapshot)
    for e in emails:
        db.add(CameraEmailNotificationRecipient(log_id=log.id, recipient_email=e))
    db.flush()

    # kirim email
    try:
        _send_email_with_image(emails, subject, body, html, snapshot_path)
        log.success = True
        log.error_message = None
        log.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Successfully sent email alert for camera %s to %s", camera.hostname, emails)
        return True
    except Exception as e:
        log.success = False
        log.error_message = str(e)
        log.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.exception("Failed to send email for camera %s (%s): %s", camera.hostname, camera.ip, e)
        return False
