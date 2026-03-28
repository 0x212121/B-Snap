import uuid
import hashlib
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

def generate_uuid():
    return str(uuid.uuid4())

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
    resolution = Column(String(12), default="Unknown")
    
    # P0-001: File hash for integrity verification
    file_hash = Column(String(64), nullable=True, index=True)
    
    # P0-002: Soft delete support
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # P2-001: Legal hold / retention hold for critical evidence
    retention_hold = Column(Boolean, default=False, server_default="0", nullable=False)
    retention_hold_reason = Column(String(500), nullable=True)
    retention_hold_by = Column(String(100), nullable=True)
    retention_hold_at = Column(DateTime(timezone=True), nullable=True)
    
    camera = relationship("Camera", back_populates="videos")
    
    @property
    def is_deleted(self):
        """Check if this video has been soft-deleted."""
        return self.deleted_at is not None
    
    def calculate_hash(self, file_path_full: str = None) -> str:
        """
        Calculate SHA-256 hash of the video file.
        
        Args:
            file_path_full: Full path to the file. If None, uses self.file_path
            
        Returns:
            Hex digest of SHA-256 hash
        """
        if file_path_full is None:
            import os
            file_path_full = os.path.join("static", "videos", self.file_path)
        
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path_full, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            from app.core.logging_config import setup_logging
            import logging
            setup_logging()
            logger = logging.getLogger("video")
            logger.error(f"Failed to calculate hash for {file_path_full}: {e}")
            return None
    
    def verify_integrity(self, file_path_full: str = None) -> bool:
        """
        Verify file integrity by comparing current hash with stored hash.
        
        Args:
            file_path_full: Full path to the file
            
        Returns:
            True if integrity verified, False otherwise
        """
        if not self.file_hash:
            return False
        
        current_hash = self.calculate_hash(file_path_full)
        return current_hash == self.file_hash
    
    def soft_delete(self):
        """Mark this video as deleted (soft delete)."""
        self.deleted_at = datetime.now(timezone.utc)
    
    def restore(self):
        """Restore a soft-deleted video."""
        self.deleted_at = None
