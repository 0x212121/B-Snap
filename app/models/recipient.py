from app.db.database import Base
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy import UniqueConstraint

class GroupRecipient(Base):
    __tablename__ = "group_recipients"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("camera_groups.id", ondelete="CASCADE"))
    email = Column(String, index=True, nullable=False)
    nickname = Column(String, nullable=True)

    group = relationship("CameraGroup", back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("group_id", "email", name="uq_group_email"),
    )

