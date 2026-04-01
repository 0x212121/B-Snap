"""
Email Template Model

Stores customizable email templates for different notification types.
"""
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.sql import func
from app.db.database import Base


class EmailTemplate(Base):
    """
    Email template for notification messages.
    
    Template types:
    - tamper_alert: Camera tampering detected (blur, dark, occluded)
    - recovery_alert: Camera image recovered from tampered state
    - offline_alert: Camera went offline
    """
    __tablename__ = "email_templates"
    
    # Template type as primary key (tamper_alert, recovery_alert, offline_alert)
    template_type = Column(String(50), primary_key=True)
    
    # Subject line with variable placeholders
    subject = Column(String(500), nullable=False)
    
    # Plain text body with variable placeholders
    plain_body = Column(Text, nullable=False)
    
    # HTML body with variable placeholders
    html_body = Column(Text, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<EmailTemplate(type='{self.template_type}')>"


# Default templates with variables as placeholders
DEFAULT_TEMPLATES = {
    "tamper_alert": {
        "subject": "⚠️ [{{camera_group}}] CCTV Alert – {{camera_name}} {{reason.upper()}}",
        "plain_body": """Yth. User,

Sistem mendeteksi bahwa CCTV {{camera_name}} (IP: {{camera_ip}}) mengalami anomali/tampering dengan indikasi: {{reason}}.
- No. Asset: {{asset_no or '-'}}
- Lokasi: {{location or '-'}}
- Koordinat: https://www.google.com/maps?q={{latitude}},{{longitude}}
- Waktu Kejadian: {{incident_time}} UTC

👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.

Catatan: Foto snapshot {{"terlampir" if has_snapshot else "tidak tersedia"}} sebagai referensi kondisi terakhir kamera.

Terima kasih atas perhatian dan kerja samanya.

Hormat kami,
IT Computer Operations
PT Kaltim Prima Coal""",
        "html_body": """<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      Sistem mendeteksi bahwa <b>CCTV {{camera_name}} (IP: {{camera_ip}})</b> mengalami 
      <b>anomali / tampering</b> dengan indikasi: <b>{{reason}}</b>.
    </p>

    <ul>
      <li><b>No. Asset:</b> {{asset_no or '-'}}</li>
      <li><b>Lokasi:</b> {{location or '-'}}</li>
      <li><b>Koordinat:</b> <a href="https://www.google.com/maps?q={{latitude}},{{longitude}}" target="_blank">Lihat di Google Maps</a></li>
      <li><b>Waktu Kejadian:</b> {{incident_time}} UTC</li>
    </ul>

    <p>
        👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.
    </p>

    <p style="font-size:13px; color:#444;">
      <b>Catatan:</b> Foto snapshot {{"terlampir" if has_snapshot else "tidak tersedia"}} sebagai referensi kondisi terakhir kamera.
    </p>

    <br/>
    <p>
      Hormat kami,<br/>
      <b>IT Computer Operations</b><br/>
      PT Kaltim Prima Coal
    </p>
  </body>
</html>"""
    },
    "recovery_alert": {
        "subject": "✅ [{{camera_group}}] CCTV Image Recovery – {{camera_name}}",
        "plain_body": """Yth. User,

CCTV {{camera_name}} (IP: {{camera_ip}}) image telah kembali NORMAL dari status TAMPERED.{% if last_reason %}
Previous issue: {{last_reason}}{% endif %}

- No. Asset: {{asset_no or '-'}}
- Lokasi: {{location or '-'}}
- Koordinat: https://www.google.com/maps?q={{latitude}},{{longitude}}
- Waktu Recovery: {{recovery_time}} UTC
- Snapshot Terakhir: {{snapshot_time or 'N/A'}}

Sistem telah memverifikasi kualitas gambar kamera telah pulih (tidak blur, gelap, atau terhalang).

Catatan: Foto snapshot {{"terlampir" if has_snapshot else "tidak tersedia"}} sebagai referensi kondisi kamera saat ini.

Terima kasih,
IT Computer Operations
PT Kaltim Prima Coal""",
        "html_body": """<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      <b>CCTV {{camera_name}} (IP: {{camera_ip}})</b> image telah kembali <b style="color: #16a34a;">NORMAL</b> dari status TAMPERED.
    </p>

    <ul>
      <li><b>No. Asset:</b> {{asset_no or '-'}}</li>
      <li><b>Lokasi:</b> {{location or '-'}}</li>
      <li><b>Koordinat:</b> <a href="https://www.google.com/maps?q={{latitude}},{{longitude}}" target="_blank">Lihat di Google Maps</a></li>
      <li><b>Waktu Recovery:</b> {{recovery_time}} UTC</li>
      <li><b>Snapshot Terakhir:</b> {{snapshot_time or 'N/A'}} {{"(foto terlampir)" if has_snapshot else ""}}</li>
      {% if last_reason %}<li><b>Previous Issue:</b> {{last_reason}}</li>{% endif %}
    </ul>

    <p>
      Sistem telah memverifikasi kualitas gambar kamera telah pulih (tidak blur, gelap, atau terhalang).
    </p>

    <p style="font-size:13px; color:#444;">
      <b>Catatan:</b> Foto snapshot {{"terlampir sebagai attachment" if has_snapshot else "tidak tersedia"}} sebagai referensi kondisi kamera saat ini.
    </p>

    <br/>
    <p>
      Hormat kami,<br/>
      <b>IT Computer Operations</b><br/>
      PT Kaltim Prima Coal
    </p>
  </body>
</html>"""
    },
    "offline_alert": {
        "subject": "🚨 [{{camera_group}}] CCTV Alert – {{camera_name}} – Offline",
        "plain_body": """Yth. User,

Sistem mendeteksi bahwa CCTV {{camera_name}} (IP: {{camera_ip}}) telah offline lebih dari {{offline_duration}} menit.
- Waktu Kejadian: {{incident_time}}
- Nomor Asset Kamera: {{asset_no or 'N/A'}}
- Koordinat Lokasi Kamera: https://www.google.com/maps?q={{latitude}},{{longitude}}
- Snapshot Terakhir: {{snapshot_time or 'Tidak tersedia'}}

👉 Mohon segera buat tiket SIHEPI dengan mencantumkan cost code agar dapat diproses oleh tim teknis/mitra terkait.

Catatan: Foto snapshot (jika tersedia) dilampirkan secara otomatis & periodik oleh sistem monitoring B-Snap sebagai referensi kondisi terakhir, bukan kondisi real-time saat kamera offline.

Terima kasih atas kerja samanya.

Hormat kami,
IT Computer Operations
PT Kaltim Prima Coal""",
        "html_body": """<html>
  <body style="font-family: Arial, sans-serif; color: #111; background-color: #ffffff; padding: 12px;">
    <p>Yth. User,</p>

    <p>
      Sistem mendeteksi bahwa <b>CCTV {{camera_name}} (IP: {{camera_ip}})</b> telah <b>offline</b> lebih dari {{offline_duration}} menit.
    </p>

    <ul>
      <li><b>Waktu Kejadian:</b> {{incident_time}}</li>
      <li><b>No. Asset:</b> {{asset_no or 'N/A'}}</li>
      <li><b>Koordinat Camera:</b> <a href="https://www.google.com/maps?q={{latitude}},{{longitude}}">https://www.google.com/maps?q={{latitude}},{{longitude}}</a></li>
      <li><b>Snapshot Terakhir:</b> {{snapshot_time or 'Tidak tersedia'}} {{"(foto terlampir)" if has_snapshot else ""}}</li>
    </ul>

    <p>
      👉 Mohon segera <b>buat tiket SIHEPI</b> dengan mencantumkan <b>cost code</b> 
      agar dapat diproses oleh tim teknis/mitra terkait.
    </p>

    <p style="font-size: 13px; color:#444;">
      <b>Catatan:</b> Foto snapshot 
      {{"terlampir sebagai attachment" if has_snapshot else "tidak tersedia"}} 
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
</html>"""
    }
}


# Available variables for each template type
TEMPLATE_VARIABLES = {
    "tamper_alert": [
        {"key": "camera_name", "label": "Camera Name", "description": "Hostname of the camera"},
        {"key": "camera_ip", "label": "Camera IP", "description": "IP address of the camera"},
        {"key": "camera_group", "label": "Camera Group", "description": "Group/Division name"},
        {"key": "reason", "label": "Tamper Reason", "description": "Type of tampering detected (blur, dark, occluded)"},
        {"key": "asset_no", "label": "Asset Number", "description": "Camera asset number"},
        {"key": "location", "label": "Location", "description": "Physical location of camera"},
        {"key": "latitude", "label": "Latitude", "description": "GPS latitude coordinate"},
        {"key": "longitude", "label": "Longitude", "description": "GPS longitude coordinate"},
        {"key": "incident_time", "label": "Incident Time", "description": "Time when tampering was detected"},
        {"key": "has_snapshot", "label": "Has Snapshot", "description": "Boolean if snapshot is available"},
    ],
    "recovery_alert": [
        {"key": "camera_name", "label": "Camera Name", "description": "Hostname of the camera"},
        {"key": "camera_ip", "label": "Camera IP", "description": "IP address of the camera"},
        {"key": "camera_group", "label": "Camera Group", "description": "Group/Division name"},
        {"key": "last_reason", "label": "Last Issue", "description": "Previous tamper reason (if any)"},
        {"key": "asset_no", "label": "Asset Number", "description": "Camera asset number"},
        {"key": "location", "label": "Location", "description": "Physical location of camera"},
        {"key": "latitude", "label": "Latitude", "description": "GPS latitude coordinate"},
        {"key": "longitude", "label": "Longitude", "description": "GPS longitude coordinate"},
        {"key": "recovery_time", "label": "Recovery Time", "description": "Time when camera recovered"},
        {"key": "snapshot_time", "label": "Snapshot Time", "description": "Time of last snapshot"},
        {"key": "has_snapshot", "label": "Has Snapshot", "description": "Boolean if snapshot is available"},
    ],
    "offline_alert": [
        {"key": "camera_name", "label": "Camera Name", "description": "Hostname of the camera"},
        {"key": "camera_ip", "label": "Camera IP", "description": "IP address of the camera"},
        {"key": "camera_group", "label": "Camera Group", "description": "Group/Division name"},
        {"key": "asset_no", "label": "Asset Number", "description": "Camera asset number"},
        {"key": "location", "label": "Location", "description": "Physical location of camera"},
        {"key": "latitude", "label": "Latitude", "description": "GPS latitude coordinate"},
        {"key": "longitude", "label": "Longitude", "description": "GPS longitude coordinate"},
        {"key": "incident_time", "label": "Incident Time", "description": "Time when camera went offline"},
        {"key": "offline_duration", "label": "Offline Duration", "description": "Duration in minutes"},
        {"key": "snapshot_time", "label": "Snapshot Time", "description": "Time of last snapshot"},
        {"key": "has_snapshot", "label": "Has Snapshot", "description": "Boolean if snapshot is available"},
    ]
}


def get_default_template(template_type: str) -> dict:
    """Get default template for a given type."""
    return DEFAULT_TEMPLATES.get(template_type, DEFAULT_TEMPLATES["tamper_alert"])


def get_template_variables(template_type: str) -> list:
    """Get available variables for a template type."""
    return TEMPLATE_VARIABLES.get(template_type, [])
