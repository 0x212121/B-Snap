import uuid
from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, Date, Float, String, Integer, DateTime, ForeignKey, Boolean, event, JSON
)
from sqlalchemy.orm import relationship
from app.db.database import Base


def generate_uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, default="user", nullable=False)
    last_login = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    token = Column(String, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_secret = Column(String, nullable=True)
    is_2fa_enabled = Column(Boolean, default=False, nullable=False)
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    web_tokens = Column(JSON, default=[])
    api_tokens = Column(JSON, default=[])

    group_id = Column(Integer, ForeignKey('camera_groups.id'), nullable=True)
    group = relationship("CameraGroup", back_populates="users")


class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(String, unique=True, nullable=False)
    user_agent = Column(String)
    ip_address = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="sessions")


class CameraGroup(Base):
    __tablename__ = "camera_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    cameras = relationship("Camera", back_populates="group") # Removed cascade
    nvr = relationship("NVR", back_populates="group") # Removed cascade
    users = relationship("User", back_populates="group") # Removed cascade


@event.listens_for(CameraGroup.__table__, "after_create")
def insert_default_groups(target, connection, **kw):
    default_groups = [
        {"id": 1, "name": "CPHD"}, {"id": 2, "name": "MSD"},
        {"id": 3, "name": "MOD"}, {"id": 4, "name": "ESD"},
        {"id": 5, "name": "SCD"}, {"id": 6, "name": "CMD"},
        {"id": 7, "name": "IT"}, {"id": 8, "name": "HR"},
        {"id": 9, "name": "MDD"}, {"id": 10, "name": "HSES"},
        {"id": 11, "name": "ALL"},
    ]
    # Check if groups already exist to prevent errors on reconnect
    for group in default_groups:
        result = connection.execute(target.select().where(target.c.id == group['id'])).scalar()
        if not result:
            connection.execute(target.insert().values(**group))


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid, unique=True)
    hostname = Column(String, unique=True, nullable=False)
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

    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    group = relationship("CameraGroup", back_populates="cameras")

    # --- CHANGE 2: Removed cascade="all, delete-orphan" ---
    # This stops SQLAlchemy from automatically deleting snapshots when a camera is deleted.
    # The database's `ondelete="SET NULL"` rule on the ForeignKey will handle it instead.
    snapshot_logs = relationship("SnapshotLog", back_populates="camera")
    health = relationship("CameraHealth", back_populates="camera", uselist=False, cascade="all, delete-orphan")
    daily_stats = relationship("CameraDailyStats", back_populates="camera", cascade="all, delete-orphan")
    snapshots = relationship("Snapshot", back_populates="camera")
    videos = relationship("Video", back_populates="camera")


class CameraHealth(Base):
    __tablename__ = "camera_health"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    status = Column(String, default="Unknown", nullable=False)
    status_changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    latency = Column(Integer)
    checked = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_online = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    type = Column(String, default="Camera", nullable=False)

    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"))
    camera = relationship("Camera", back_populates="health")

    nvr_id = Column(String(36), ForeignKey("nvr.id", ondelete="CASCADE"))
    nvr = relationship("NVR", back_populates="health")


class Configuration(Base):
    __tablename__ = "configurations"

    key = Column(String, primary_key=True)
    value = Column(String)


class HealthCheckStatus(Base):
    __tablename__ = "health_check_status"

    id = Column(Integer, primary_key=True, default=1)
    is_running = Column(Boolean, default=False)
    start_time = Column(DateTime(timezone=True))
    total_cameras = Column(Integer, default=0)
    completed_cameras = Column(Integer, default=0)


class CameraDailyStats(Base):
    __tablename__ = "camera_daily_stats"

    id = Column(Integer, primary_key=True)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    camera_name = Column(String(60), nullable=False)
    date = Column(Date, nullable=False, default=date.today)
    uptime_percentage = Column(Float, default=0.0)
    snapshot_count = Column(Integer, default=0)
    total_uptime_seconds = Column(Integer, default=0)
    total_downtime_seconds = Column(Integer, default=0)

    camera = relationship("Camera", back_populates="daily_stats")


class NVR(Base):
    __tablename__ = "nvr"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hostname = Column(String(100), nullable=False)
    ip = Column(String(45), nullable=False)
    username = Column(String(100))
    password = Column(String(100))
    location = Column(String(25))
    status = Column(String(15))
    asset_no = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)

    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    group = relationship("CameraGroup", back_populates="nvr")

    health = relationship("CameraHealth", back_populates="nvr", uselist=False, cascade="all, delete-orphan")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    
    # --- CHANGE 1: Made camera_id nullable and added ondelete rule ---
    # `nullable=True` allows this field to be empty.
    # `ondelete="SET NULL"` tells the database to set this field to NULL if the linked camera is deleted.
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)

    camera_name = Column(String, nullable=False)
    camera_ip = Column(String, nullable=False)
    camera_port = Column(Integer, default=80)
    camera_location = Column(String)
    camera_group = Column(String)

    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    file_path = Column(String, nullable=False)
    file_size = Column(Integer)
    resolution = Column(String)

    camera = relationship("Camera", back_populates="snapshots")


class Video(Base):
    __tablename__ = "videos"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)

    camera_name = Column(String, nullable=False)
    camera_ip = Column(String, nullable=True)
    camera_group = Column(String, nullable=True)

    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    file_path = Column(String, nullable=False, unique=True)
    file_size = Column(Integer)
    duration = Column(Integer)

    camera = relationship("Camera", back_populates="videos")


class SnapshotLog(Base):
    __tablename__ = "snapshot_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid, index=True)
    # This log should also persist, so we set its foreign key to null on delete.
    camera_name = Column(String, ForeignKey("cameras.hostname", ondelete="SET NULL"), nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    camera = relationship("Camera", back_populates="snapshot_logs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=False)
    ip = Column(String)
    extra = Column(String)
