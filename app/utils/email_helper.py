import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER)

def send_email(to: list[str], subject: str, body: str, html: str | None = None):
    """
    Kirim email ke satu atau banyak penerima.
    """
    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject

    # isi email
    msg.attach(MIMEText(body, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, to, msg.as_string())
