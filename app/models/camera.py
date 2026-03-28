from app.db.database import Base
from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey, event, text, Text
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import expression
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


# Event listener to mark snapshots as orphaned before camera delete
@event.listens_for(Camera, 'before_delete')
def mark_snapshots_orphaned(mapper, connection, target):
    """Mark all snapshots as orphaned before camera is deleted."""
    connection.execute(
        text("UPDATE snapshots SET is_orphaned = TRUE WHERE camera_id = :camera_id"),
        {"camera_id": target.id}
    )
