"""Model for tracking orphaned snapshot files.

This model tracks snapshot files that exist on disk but have no corresponding
record in the database (truly orphaned files).
"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, BigInteger
from app.db.database import Base


class OrphanedFile(Base):
    """Tracks snapshot files without database records.
    
    These are files that exist in the snapshots folder but have no
corresponding entry in the snapshots table.
    """
    __tablename__ = "orphaned_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String(500), nullable=False, unique=True, index=True)
    file_size = Column(BigInteger, nullable=True)
    camera_id = Column(String(36), nullable=True, index=True)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Status: 'pending', 'reviewed', 'deleted', 'restored'
    status = Column(String(20), default='pending', index=True)
    
    # Optional: notes for admin review
    notes = Column(String(500), nullable=True)
    
    # When this record was last updated
    updated_at = Column(DateTime(timezone=True), 
                       default=lambda: datetime.now(timezone.utc),
                       onupdate=lambda: datetime.now(timezone.utc))
