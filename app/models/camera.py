from app.db.database import Base
from sqlalchemy import Column, String, Integer, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import expression
import uuid

def generate_uuid():
    return str(uuid.uuid4())

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid, unique=True)
    hostname = Column(String, unique=True, index=True, nullable=False)
    previous_name = Column(String)
    ip = Column(String)
    port = Column(Integer, default=80)
    username = Column(String)
    password = Column(String)
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
    
    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    group = relationship("CameraGroup", back_populates="cameras")

    snapshot_logs = relationship("SnapshotLog", back_populates="camera", cascade="all, delete-orphan")
    health = relationship("CameraHealth", back_populates="camera", uselist=False, cascade="all, delete-orphan")
    daily_stats = relationship("CameraDailyStats", back_populates="camera", cascade="all, delete-orphan")
    snapshots = relationship("Snapshot", back_populates="camera", cascade="all, delete-orphan")
    videos = relationship("Video", back_populates="camera", cascade="all, delete-orphan")

    email_logs = relationship(
        "CameraEmailNotificationLog",
        back_populates="camera",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
