from email.mime.image import MIMEImage
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


def _send_email_with_image(to_emails: List[str], subject: str, body: str, html: str, image_path: str = None):
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to_emails)

    # buat versi text & html
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain"))

    # tambahkan <img> CID jika ada snapshot
    html_content = html
    if image_path:
        html_content += "<p><img src='cid:snapshot1' style='max-width:600px; border:1px solid #ccc; border-radius:6px;'/></p>"

    alt.attach(MIMEText(html_content, "html"))
    msg.attach(alt)

    # attach gambar sebagai MIMEImage
    if image_path:
        with open(image_path, "rb") as f:
            img = MIMEImage(f.read(), name="snapshot.jpg")
        img.add_header("Content-ID", "<snapshot1>")
        img.add_header("Content-Disposition", "inline", filename="snapshot.jpg")
        msg.attach(img)

    # kirim
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, to_emails, msg.as_string())