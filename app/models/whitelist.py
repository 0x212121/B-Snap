from datetime import datetime, timezone
from enum import Enum as PyEnum  # Hindari conflict nama dengan SQLAlchemy Enum
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    DateTime,
    Index,
    Enum as SAEnum,  # Enum untuk SQLAlchemy
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base


class RoleEnum(str, PyEnum):
    admin = "admin"
    user = "user"


class WhatsappWhitelist(Base):
    __tablename__ = "whatsapp_whitelist"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=True)
    role = Column(SAEnum(RoleEnum, name="role_enum"), default=RoleEnum.user, nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    group_id = Column(Integer, ForeignKey("camera_groups.id", ondelete="SET NULL"), nullable=True)
    group = relationship("CameraGroup", back_populates="whatsapp_whitelist", lazy="joined")


    __table_args__ = (
        Index("idx_whatsapp_phone_group", "phone_number", "group_id"),
    )

    @property
    def group_name(self):
        return self.group.name if self.group else None
