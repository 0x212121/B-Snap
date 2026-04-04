from sqlalchemy import Column, Integer, String, event
from sqlalchemy.orm import relationship
from app.db.database import Base

default_groups = [
    {"id": 1, "name": "CPHD"}, {"id": 2, "name": "MSD"},
    {"id": 3, "name": "MOD"}, {"id": 4, "name": "ESD"},
    {"id": 5, "name": "SCD"}, {"id": 6, "name": "CMD"},
    {"id": 7, "name": "IT"}, {"id": 8, "name": "HR"},
    {"id": 9, "name": "MDD"}, {"id": 10, "name": "HSES"},
    {"id": 11, "name": "ALL"},
]

class CameraGroup(Base):
    __tablename__ = "camera_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    cameras = relationship("Camera", back_populates="group")  # pakai string
    nvr = relationship("NVR", back_populates="group")
    users = relationship("User", back_populates="group")
    whatsapp_whitelist = relationship("WhatsappWhitelist", back_populates="group", cascade="all, delete-orphan")
    recipients = relationship("GroupRecipient", back_populates="group", cascade="all, delete-orphan")


@event.listens_for(CameraGroup.__table__, "after_create")
def insert_default_groups(target, connection, **kw):
    for group in default_groups:
        result = connection.execute(target.select().where(target.c.id == group['id'])).scalar()
        if not result:
            connection.execute(target.insert().values(**group))
