from sqlalchemy import Column, String
from app.db.database import Base

class Configuration(Base):
    __tablename__ = "configurations"
    key = Column(String, primary_key=True)
    value = Column(String)
