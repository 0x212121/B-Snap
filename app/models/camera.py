from app.db.database import Base
from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey, event, text, Text, DateTime
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import expression
from datetime import datetime, timezone
import uuid
import logging

def generate_uuid():
    return str(uuid.uuid4())

logger = logging.getLogger("camera")

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid, unique=True)
    hostname = Column(String, unique=True, index=True, nullable=False)
    previous_name = Column(String)
    ip = Column(String)
    port = Column(Integer, default=80)
    username = Column(String)
    # P1-001: Changed to Text to accommodate encrypted passwords
    _password = Column("password", Text, nullable=True)
    latitude = Column(Float)
    longitude = Column(Float)
    previous_latitude = Column(Float)
    previous_longitude = Column(Float)
    asset_no = Column(String)
    location = Column(String)
    status = Column(String)
    is_flipped = Column(Boolean, default=False, server_default=expression.false())
    note = Column(String, nullable=True)
    snapshot_url = Column(String(255), nullable=True)
    
    # P2-002: Safety classification for mining operations
    safety_classification = Column(String(20), default='standard', nullable=False, server_default='standard')
    
    # Email Notification Circuit Breaker Fields
    notification_fail_count = Column(Integer, default=0, server_default='0', nullable=False)
    notification_suppressed_until = Column(DateTime(timezone=True), nullable=True)
    last_notification_at = Column(DateTime(timezone=True), nullable=True)
    
    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    group = relationship("CameraGroup", back_populates="cameras")

    snapshot_logs = relationship("SnapshotLog", back_populates="camera", cascade="all, delete-orphan")
    health = relationship("CameraHealth", back_populates="camera", uselist=False, cascade="all, delete-orphan")
    daily_stats = relationship("CameraDailyStats", back_populates="camera", cascade="all, delete-orphan")
    snapshots = relationship("Snapshot", back_populates="camera", passive_deletes=True)
    videos = relationship("Video", back_populates="camera", cascade="all, delete-orphan")

    email_logs = relationship(
        "CameraEmailNotificationLog",
        back_populates="camera",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    
    # P1-001: Password encryption property
    @property
    def password(self):
        """Get decrypted password."""
        if self._password:
            try:
                from app.utils.encryption import decrypt_from_db
                return decrypt_from_db(self._password)
            except Exception as e:
                logger.warning(f"Failed to decrypt password for camera {self.hostname}: {e}")
                return self._password
        return self._password
    
    @password.setter
    def password(self, value):
        """Set encrypted password."""
        if value:
            try:
                from app.utils.encryption import encrypt_for_db
                self._password = encrypt_for_db(value)
            except Exception as e:
                logger.warning(f"Failed to encrypt password for camera {self.hostname}: {e}")
                self._password = value
        else:
            self._password = value
    
    def get_password_encrypted(self):
        """Get the raw encrypted password (for API responses - admin only)."""
        return self._password
    
    @validates('safety_classification')
    def validate_safety_classification(self, key, value):
        """Validate safety classification value."""
        valid_values = ['critical', 'standard', 'low']
        if value not in valid_values:
            raise ValueError(f"Invalid safety_classification. Must be one of: {', '.join(valid_values)}")
        return value
    
    @validates('latitude')
    def validate_latitude(self, key, value):
        """MED-004: Validate latitude is within valid range (-90 to 90)."""
        if value is not None:
            try:
                lat = float(value)
                if lat < -90 or lat > 90:
                    raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
                return lat
            except (TypeError, ValueError) as e:
                if "Latitude must be between" in str(e):
                    raise
                raise ValueError(f"Invalid latitude value: {value}")
        return value
    
    @validates('longitude')
    def validate_longitude(self, key, value):
        """MED-004: Validate longitude is within valid range (-180 to 180)."""
        if value is not None:
            try:
                lon = float(value)
                if lon < -180 or lon > 180:
                    raise ValueError(f"Longitude must be between -180 and 180, got {lon}")
                return lon
            except (TypeError, ValueError) as e:
                if "Longitude must be between" in str(e):
                    raise
                raise ValueError(f"Invalid longitude value: {value}")
        return value


# Event listener to mark snapshots as orphaned before camera delete
@event.listens_for(Camera, 'before_delete')
def mark_snapshots_orphaned(mapper, connection, target):
    """Mark all snapshots as orphaned before camera is deleted."""
    connection.execute(
        text("UPDATE snapshots SET is_orphaned = TRUE WHERE camera_id = :camera_id"),
        {"camera_id": target.id}
    )
