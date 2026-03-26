import uuid
import logging
from sqlalchemy import Column, String, Integer, Float, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.db.database import Base

logger = logging.getLogger("nvr")

def generate_uuid():
    return str(uuid.uuid4())

class NVR(Base):
    __tablename__ = "nvr"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hostname = Column(String(100), unique=True, index=True, nullable=False)
    ip = Column(String(45), nullable=False)
    username = Column(String(100))
    # Password encrypted with AES-256-GCM
    _password = Column("password", Text, nullable=True)
    location = Column(String(100))
    status = Column(String(15))
    asset_no = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    group_id = Column(Integer, ForeignKey('camera_groups.id'))
    note = Column(String, nullable=True)
    group = relationship("CameraGroup", back_populates="nvr")
    health = relationship("CameraHealth", back_populates="nvr", uselist=False, cascade="all, delete-orphan")
    
    @property
    def password(self):
        """Get decrypted password."""
        if self._password:
            try:
                from app.utils.encryption import decrypt_from_db
                return decrypt_from_db(self._password)
            except Exception as e:
                logger.warning(f"Failed to decrypt password for NVR {self.hostname}: {e}")
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
                logger.warning(f"Failed to encrypt password for NVR {self.hostname}: {e}")
                self._password = value
        else:
            self._password = value
    
    def get_password_encrypted(self):
        """Get the raw encrypted password (for API responses - admin only)."""
        return self._password