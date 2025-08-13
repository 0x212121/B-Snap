import uuid
from sqlalchemy import Column, String, Integer, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class NVR(Base):
    __tablename__ = "nvr"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hostname = Column(String(100), unique=True, index=True, nullable=False)
    ip = Column(String(45), nullable=False)
    username = Column(String(100))
    password = Column(String(100))
    location = Column(String(100))
    status = Column(String(15))
    asset_no = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    note = Column(String, nullable=True)
    group = relationship("CameraGroup", back_populates="nvr")
    health = relationship("CameraHealth", back_populates="nvr", uselist=False, cascade="all, delete-orphan")