import logging
from datetime import datetime, timezone, timedelta
import os
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

# ambil semua dari email_helper (sudah include get_recipients_for_camera)
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
set_debug_mode(True)

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
            logger.info(
                "Email log already exists but marked failed for camera %s (incident %s). Skip re-sending.",
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
        plain_body, html_body = build_email_body(
            camera_name=camera.hostname,
            ip=camera.ip,
            asset_no=camera.asset_no or "N/A",
            coordinate=f'https://www.google.com/maps?q={camera.latitude},{camera.longitude}' or "N/A",
            incident_time=local_incident,
            last_snapshot_time=snapshot_time,
            has_snapshot=bool(snapshot_path)
        )

        _send_email_with_image(
            to_emails=emails,
            subject=f"🚨 [{camera_group}] CCTV Alert – {camera.hostname} – Offline",
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
        logger.info(
            "Successfully sent email alert for camera %s to %s",
            camera.hostname,
            emails,
        )
        return True
    except Exception as e:
        log.success = False
        log.error_message = str(e)
        log.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.exception(
            "Failed to send email for camera %s (%s): %s",
            camera.hostname,
            camera.ip,
            e,
        )
        return False


def send_tamper_alert(db: Session, camera, reason: str, snapshot_path: str):
    logger.info("[EMAIL_DEBUG] send_tamper_alert triggered for %s (%s)", camera.hostname, reason)
    camera_group = camera.group.name if camera.group else "No Division"
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        logger.warning("No recipients found for %s", camera.hostname)
        return False

    subject = f"⚠️ [{camera_group}] CCTV Alert – {camera.hostname} {reason.upper()}"

    # === Plain text body ===
    plain_body = f"""
Yth. User,

Sistem mendeteksi bahwa CCTV {camera.hostname} (IP: {camera.ip}) mengalami anomali/tampering dengan indikasi: {reason}.
- No. Asset: {camera.asset_no or '-'}
- Lokasi: {camera.location or '-'}
- Group: {camera.group.name if camera.group else '-'}
- Waktu Kejadian: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} 

👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.

Catatan: Foto snapshot { "terlampir" if snapshot_path else "tidak tersedia" } sebagai referensi kondisi terakhir kamera.

Terima kasih atas perhatian dan kerja samanya.

Hormat kami,
IT Computer Operations
PT Kaltim Prima Coal
""".strip()

    # === HTML body ===
    html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      Sistem mendeteksi bahwa <b>CCTV {camera.hostname} (IP: {camera.ip})</b> mengalami 
      <b>anomali / tampering</b> dengan indikasi: <b>{reason}</b>.
    </p>

    <ul>
      <li><b>No. Asset:</b> {camera.asset_no or '-'}</li>
      <li><b>Lokasi:</b> {camera.location or '-'}</li>
      <li><b>Group:</b> {camera.group.name if camera.group else '-'}</li>
      <li><b>Waktu Kejadian:</b> {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</li>
    </ul>

    <p>
        👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.
    </p>

    <p style="font-size:13px; color:#444;">
      <b>Catatan:</b> Foto snapshot { "terlampir" if snapshot_path else "tidak tersedia" } sebagai referensi kondisi terakhir kamera.
    </p>

    <p>Terima kasih atas perhatian dan kerja samanya.</p>

    <p style="line-height:1.6; margin:0;">
      Hormat kami,<br>
      <b>IT Computer Operations</b><br>
      PT Kaltim <span style="color:#e60000; font-weight:bold;">Prima</span> Coal
    </p>
  </body>
</html>
"""

    # === Buat log DB ===
    log_entry = CameraEmailNotificationLog(
        camera_id=camera.id,
        camera_name=camera.hostname,
        incident_started_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        success=False,
        error_message=None,
        reason="tampered",
    )
    db.add(log_entry)
    db.flush()

    for e in recipients:
        db.add(CameraEmailNotificationRecipient(log_id=log_entry.id, recipient_email=e))
    db.flush()

    try:
        _send_email_with_image(
            to_emails=recipients,
            subject=subject,
            cam_group=camera_group,
            cam_hostname=camera.hostname,
            snapshot_time=datetime.now(),
            body=plain_body,
            html=html_body,
            image_path=snapshot_path,
        )

        log_entry.success = True
        log_entry.error_message = None
        log_entry.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Tamper alert email sent for %s → %s", camera.hostname, recipients)
        return True

    except Exception as e:
        log_entry.success = False
        log_entry.error_message = str(e)
        log_entry.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.exception("Failed to send tamper alert for %s: %s", camera.hostname, e)
        return False


def send_recovery_alert(db: Session, camera):
    camera_group = camera.group.name if camera.group else "No Division"
    recipients = get_recipients_for_camera(db, camera)
    if not recipients:
        logger.warning("No recipients found for %s (recovery)", camera.hostname)
        return False

    subject = f"✅ [{camera_group}] CCTV Recovery – {camera.hostname} back to normal"

    plain_body = f"""
Yth. User,

Kamera {camera.hostname} (IP: {camera.ip}) telah kembali normal dan terhubung dengan sistem B-Snap.

Mohon update tiket atau status SIHEPI yang sebelumnya terkait kamera ini jika sudah selesai ditangani.

Terima kasih atas kerja samanya.

Hormat kami,
IT Computer Operations
PT Kaltim Prima Coal
""".strip()

    html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      🎉 <b>CCTV {camera.hostname} (IP: {camera.ip})</b> telah kembali <b>normal</b> dan 
      kembali terhubung dengan sistem <b>B-Snap</b>.
    </p>

    <p>
      Mohon update tiket atau status SIHEPI yang sebelumnya terkait kamera ini apabila sudah ditutup atau selesai ditangani.
    </p>

    <p>Terima kasih atas kerja samanya.</p>

    <p style="line-height:1.6; margin:0;">
      Hormat kami,<br>
      <b>IT Computer Operations</b><br>
      PT Kaltim <span style="color:#e60000; font-weight:bold;">Prima</span> Coal
    </p>
  </body>
</html>
"""

    # === Buat log DB ===
    log_entry = CameraEmailNotificationLog(
        camera_id=camera.id,
        camera_name=camera.hostname,
        incident_started_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
        success=False,
        error_message=None,
        reason="recovery",
    )
    db.add(log_entry)
    db.flush()

    for e in recipients:
        db.add(CameraEmailNotificationRecipient(log_id=log_entry.id, recipient_email=e))
    db.flush()

    try:
        _send_email_with_image(
            to_emails=recipients,
            subject=subject,
            cam_group=camera_group,
            cam_hostname=camera.hostname,
            snapshot_time=datetime.now(),
            body=plain_body,
            html=html_body,
        )

        log_entry.success = True
        log_entry.error_message = None
        log_entry.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Recovery email sent for %s → %s", camera.hostname, recipients)
        return True

    except Exception as e:
        log_entry.success = False
        log_entry.error_message = str(e)
        log_entry.sent_at = datetime.now(timezone.utc)
        db.commit()
        logger.exception("Failed to send recovery email for %s: %s", camera.hostname, e)
        return False
