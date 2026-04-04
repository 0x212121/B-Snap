from datetime import datetime
from email.mime.application import MIMEApplication
import os
import smtplib
import socket
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import BackgroundTasks
from typing import List, Optional
from sqlalchemy.orm import Session

# Import SMTP config helper (with DB + env fallback)
from app.utils.smtp_config import get_smtp_config

# Note: SMTP settings are now loaded from database via get_smtp_config()
# Environment variables serve as fallback only
# Use /config page to configure SMTP settings

# =========================
# EMAIL CORE FUNCTIONS
# =========================

def _send_email_sync(to: list[str], subject: str, body: str, html: str | None = None):
    """
    Kirim email secara sinkron (langsung).
    """
    if not to:
        raise ValueError("Recipient list is empty")
    
    # Load SMTP config from database (with env fallback)
    config = get_smtp_config()
    
    if not config["is_configured"]:
        raise RuntimeError("SMTP not configured. Please configure SMTP settings in /config page.")

    msg = MIMEMultipart("alternative")
    msg["From"] = config["email_from"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    # Isi plain text
    msg.attach(MIMEText(body, "plain"))
    # Isi HTML kalau ada
    if html:
        msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as server:
            server.starttls()
            server.login(config["smtp_user"], config["smtp_pass"])
            server.sendmail(config["email_from"], to, msg.as_string())
    except socket.gaierror as e:
        raise RuntimeError(f"Cannot resolve SMTP host '{config['smtp_host']}': {e}. Please check your SMTP configuration.")
    except socket.timeout as e:
        raise RuntimeError(f"SMTP connection timeout: {e}. Please check your network connection.")
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP error: {e}")


def send_email(
    to: list[str],
    subject: str,
    body: str,
    html: str | None = None,
    background_tasks: BackgroundTasks | None = None,
):
    """
    Fungsi utama kirim email.
    - Jika background_tasks diberikan → jalan async (tidak blokir).
    - Jika tidak → jalan sinkron (langsung).
    """
    if background_tasks:
        background_tasks.add_task(_send_email_sync, to, subject, body, html)
    else:
        _send_email_sync(to, subject, body, html)


def build_email_body(
    camera_name: str,
    ip: str,
    asset_no: str,
    coordinate: str,
    incident_time: str,
    last_snapshot_time: str,
    has_snapshot: bool
) -> tuple[str, str]:
    """
    Return tuple (plain_text, html_text) untuk body email CCTV offline alert.
    """

    # fallback text kalau snapshot gak ada
    snapshot_text = last_snapshot_time if has_snapshot else "Tidak tersedia"

    plain_body = f"""
Yth. User,

Sistem mendeteksi bahwa CCTV {camera_name} (IP: {ip}) telah offline lebih dari 30 menit.
- Waktu Kejadian: {incident_time}
- Nomor Asset Kamera: {asset_no}
- Koordinat Lokasi Kamera: {coordinate}
- Snapshot Terakhir: {snapshot_text}

👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.

Catatan: Foto snapshot (jika tersedia) dilampirkan secara otomatis & periodik oleh sistem monitoring B-Snap sebagai referensi kondisi terakhir, bukan kondisi real-time saat kamera offline.

Terima kasih atas kerja samanya.

Hormat kami,
IT Computer Operations
PT Kaltim Prima Coal
""".strip()
    
    snapshot_info = f"{snapshot_text} (foto terlampir)" if has_snapshot else snapshot_text

    html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      Sistem mendeteksi bahwa <b>CCTV {camera_name} (IP: {ip})</b> telah <b>offline</b> lebih dari 30 menit.
    </p>

    <ul>
      <li><b>Waktu Kejadian:</b> {incident_time}</li>
      <li><b>No. Asset:</b> {asset_no}</li>
      <li><b>Koordinat Camera:</b> <a href="{coordinate}">{coordinate}</a></li>
      <li><b>Snapshot Terakhir:</b> {snapshot_info}</li>
    </ul>

    <p>
      👉 Mohon segera <b>buat tiket SIHEPI</b> dengan mencantumkan <b>cost code</b> 
      agar dapat diproses oleh tim teknis/mitra terkait.
    </p>

    <p style="font-size: 13px; color:#444;">
      <b>Catatan:</b> Foto snapshot 
      { "terlampir sebagai attachment" if has_snapshot else "tidak tersedia" } 
      dan diambil <b>secara otomatis &amp; periodik</b> oleh sistem monitoring B-Snap sebagai referensi 
      <b>kondisi terakhir</b>, bukan kondisi real-time saat kamera offline.
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

    return plain_body, html_body


def _send_email_with_image(
    to_emails: List[str],
    subject: str,
    cam_group: str,
    cam_hostname: str,
    snapshot_time: datetime,
    body: str,
    html: str,
    image_path: str = None
) -> Optional[str]:
    """
    Kirim email dengan plain text + HTML.
    Snapshot (jika ada) dikirim sebagai attachment, bukan inline.
    
    ✅ RETURNS: CC email address yang digunakan, atau None jika tidak ada.
    """
    # Load SMTP config from database (with env fallback)
    config = get_smtp_config()
    
    if not config["is_configured"]:
        raise RuntimeError("SMTP not configured. Please configure SMTP settings in /config page.")
    
    msg = MIMEMultipart("mixed")  # mixed = bisa ada lampiran
    msg["Subject"] = subject
    msg["From"] = config["email_from"]
    msg["To"] = ", ".join(to_emails)

    # ✅ tambahkan CC helpdesk jika diset
    cc_email = config.get("email_cc")
    if cc_email and isinstance(cc_email, str) and cc_email.strip():
        msg["Cc"] = cc_email.strip()

    # alternative part: plain + html
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    # attach snapshot sebagai file (bukan inline)
    if image_path and os.path.exists(image_path):
        ts_str = snapshot_time.strftime("%Y%m%d-%H%M%Z") if snapshot_time else datetime.now().strftime("%Y%m%d-%H%M%Z")
        snapshot_file_name = f"B-Snap_{cam_group if cam_group else 'NoGroup'}_{cam_hostname}_LastSnapshot_{ts_str}.jpg"
        
        with open(image_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=snapshot_file_name)
        part['Content-Disposition'] = f'attachment; filename="{snapshot_file_name}"'
        msg.attach(part)

    # gabungkan recipients (to + cc) untuk pengiriman
    all_recipients = to_emails + ([cc_email.strip()] if cc_email and isinstance(cc_email, str) else [])

    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as server:
            server.starttls()
            server.login(config["smtp_user"], config["smtp_pass"])
            server.sendmail(config["email_from"], all_recipients, msg.as_string())
    except socket.gaierror as e:
        raise RuntimeError(f"Cannot resolve SMTP host '{config['smtp_host']}': {e}. Please check your SMTP configuration.")
    except socket.timeout as e:
        raise RuntimeError(f"SMTP connection timeout: {e}. Please check your network connection.")
    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP error: {e}")
    
    # ✅ RETURN CC email untuk keperluan logging
    return cc_email.strip() if cc_email and isinstance(cc_email, str) and cc_email.strip() else None


# =========================
# RECIPIENT HELPER (gabungan recipient_utils)
# =========================

from app.models.recipient import GroupRecipient
from app.models.camera import Camera as DBCamera

def _normalize(s: str) -> str:
    return s.strip().lower()

def parse_locations(text: str) -> List[str]:
    if not text:
        return []
    return [_normalize(p) for p in text.split(",") if p.strip()]


def get_recipients_for_camera(db: Session, camera: DBCamera) -> List[str]:
    """
    Return list of unique recipient emails for given camera.
    Matching rules:
      - Hanya recipients dengan group_id == camera.group_id
      - Jika recipient punya daftar locations, maka harus cocok dengan camera.location
      - Jika recipient tidak punya locations, tetap masuk (berdasarkan group)
    """
    if not camera.group_id:
        return []

    cam_loc = _normalize(camera.location) if camera.location else None
    recipients = []

    group_recs = db.query(GroupRecipient).filter(GroupRecipient.group_id == camera.group_id).all()
    for r in group_recs:
        if not r.email:
            continue

        # lokasi tidak diset → valid (artinya berlaku untuk semua lokasi group itu)
        if not r.locations:
            recipients.append(r.email)
            continue

        # lokasi diset → cek cocok tidak
        locs = parse_locations(r.locations)
        if cam_loc and cam_loc in locs:
            recipients.append(r.email)

    # dedup
    seen = set()
    return [e for e in recipients if not (e in seen or seen.add(e))]