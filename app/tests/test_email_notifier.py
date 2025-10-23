from datetime import datetime, timezone
from unittest.mock import MagicMock
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

# Muat file .env dari root project
from app.utils import email_notifier
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

print("SMTP_HOST =", os.getenv("SMTP_HOST"))


# === Mock DB session ===
mock_db = MagicMock(spec=Session)
mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
mock_db.query.return_value.filter_by.return_value.first.return_value = None

# === Dummy entities ===
class DummyGroup:
    name = "IT"

class DummyCamera:
    id = "1"
    hostname = "M3 - Parking"
    ip = "172.16.3.53"
    group = DummyGroup()
    location = "M3"
    asset_no = ""
    coordinate = "-0.123,117.123"

camera = DummyCamera()

# === Mock helper functions ===
# build_email_body biasanya menerima argumen: camera_name, ip, asset_no, coordinate, dll.
def fake_build_email_body(**kwargs):
    print("\n[MOCK BUILD EMAIL BODY]")
    for k, v in kwargs.items():
        print(f"  {k}: {v}")
    plain = f"Camera {kwargs.get('camera_name')} offline.\nAsset: {kwargs.get('asset_no')}\nCoordinate: {kwargs.get('coordinate')}"
    html = f"""
        <h3>CCTV Alert</h3>
        <p><b>Camera:</b> {kwargs.get('camera_name')}<br>
           <b>IP:</b> {kwargs.get('ip')}<br>
           <b>Asset:</b> {kwargs.get('asset_no')}<br>
           <b>Coordinate:</b> {kwargs.get('coordinate')}<br>
           <b>Incident:</b> {kwargs.get('incident_time')}<br>
           <b>Last Snapshot:</b> {kwargs.get('last_snapshot_time')}<br>
        </p>
    """
    return plain, html

email_notifier.build_email_body = fake_build_email_body
email_notifier.get_recipients_for_camera = MagicMock(return_value=["indra.wijaya@kpc.co.id"])

# Mock pengirim email agar tidak kirim sungguhan
# def fake_send_email_with_image(**kwargs):
#     print("\n[MOCK SMTP SEND]")
#     print(f"To: {kwargs.get('to_emails')}")
#     print(f"Subject: {kwargs.get('subject')}")
#     print(f"Snapshot Path: {kwargs.get('image_path')}")
#     print(f"Body (plain): {kwargs.get('body')[:60]}...")
#     print(f"Body (html): {kwargs.get('html')[:60]}...")
#     return True

# email_notifier._send_email_with_image = fake_send_email_with_image

# === Jalankan ===
incident_time = datetime.now(timezone.utc)
result = email_notifier.send_offline_incident_email_once(
    db=mock_db,
    camera=camera,
    incident_started_at=incident_time,
    offline_duration_seconds=300,
)

print("\n=== RESULT ===")
print("Success flag:", result)
print("Recipients:", email_notifier.get_recipients_for_camera.return_value)
