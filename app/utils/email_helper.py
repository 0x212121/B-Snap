from datetime import datetime
from email.mime.application import MIMEApplication
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import BackgroundTasks
from typing import List

# Load dari environment (atur di .env)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)


def _send_email_sync(to: list[str], subject: str, body: str, html: str | None = None):
    """
    Kirim email secara sinkron (langsung).
    """
    if not to:
        raise ValueError("Recipient list is empty")

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    # Isi plain text
    msg.attach(MIMEText(body, "plain"))
    # Isi HTML kalau ada
    if html:
        msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, to, msg.as_string())


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


def build_email_body(camera_name: str, ip: str, incident_time: str, last_snapshot_time: str, has_snapshot: bool) -> tuple[str, str]:
    """
    Return tuple (plain_text, html_text) untuk body email CCTV offline alert.
    """

    # fallback text kalau snapshot gak ada
    snapshot_text = last_snapshot_time if has_snapshot else "Tidak tersedia"

    plain_body = f"""
Yth. User,

Sistem mendeteksi bahwa CCTV {camera_name} (IP: {ip}) telah offline lebih dari 30 menit.

- Waktu Kejadian: {incident_time}
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
):
    """
    Kirim email dengan plain text + HTML.
    Snapshot (jika ada) dikirim sebagai attachment, bukan inline.
    """
    msg = MIMEMultipart("mixed")  # mixed = bisa ada lampiran
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to_emails)

    # ✅ tambahkan CC helpdesk
    # helpdesk = "help.desk@kpc.co.id"
    # msg["Cc"] = helpdesk

    # alternative part: plain + html
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    # attach snapshot sebagai file (bukan inline)
    if image_path and os.path.exists(image_path):
        # format snapshot_time ke YYYYMMDD-HHmmWITA
        ts_str = snapshot_time.strftime("%Y%m%d-%H%M%Z")
        # bikin nama file sesuai format
        snapshot_file_name = f"B-Snap_{cam_group if cam_group else 'NoGroup'}_{cam_hostname}_LastSnapshot_{ts_str}.jpg"
        
        with open(image_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=snapshot_file_name)
        part['Content-Disposition'] = f'attachment; filename="{snapshot_file_name}"'
        msg.attach(part)

    # gabungkan recipients (to + cc)
    all_recipients = to_emails

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, all_recipients, msg.as_string())

