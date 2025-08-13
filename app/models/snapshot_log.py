import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class SnapshotLog(Base):
    __tablename__ = "snapshot_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid, index=True)
    
    # --- PERUBAHAN UTAMA DI SINI ---
    # 1. Hapus kolom 'camera_name' yang lama.
    # camera_name = Column(String, ForeignKey("cameras.hostname", ondelete="SET NULL"), nullable=True)
    
    # 2. Tambahkan kolom 'camera_id' yang baru dan benar.
    #    Kolom ini tidak boleh NULL dan terhubung ke 'cameras.id'.
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    # -----------------------------

    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relasi 'camera' tidak perlu diubah, SQLAlchemy cukup pintar untuk menanganinya.
    camera = relationship("Camera", back_populates="snapshot_logs")