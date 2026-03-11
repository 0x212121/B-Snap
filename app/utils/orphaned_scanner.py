"""Utilities for scanning and managing orphaned snapshot files.

This module provides two approaches:
1. Folder Scanner: Scan filesystem vs database records
2. OrphanedFile Manager: Track and manage orphaned files
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Dict, Set
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.snapshot import Snapshot
from app.models.orphaned_file import OrphanedFile
from app.utils.snapshot_utils import SNAPSHOT_BASE_DIR

logger = logging.getLogger(__name__)


# ============================================================================
# PENDEKATAN 1: FOLDER SCANNER
# ============================================================================

class FolderScanner:
    """Scanner untuk mencocokkan folder/files dengan database records.
    
    Cara kerja:
    1. Scan semua file di SNAPSHOT_BASE_DIR
    2. Cocokkan dengan records di tabel snapshots
    3. File yang tidak ada di DB = orphaned
    """
    
    def __init__(self, base_dir: str = SNAPSHOT_BASE_DIR):
        self.base_dir = Path(base_dir)
        
    def get_all_files_on_disk(self) -> List[Path]:
        files = []
        if not self.base_dir.exists():
            return files
            
        for ext in ['*.jpg', '*.jpeg', '*.png']:
            try:
                files.extend(self.base_dir.rglob(ext))
            except (PermissionError, OSError) as e:
                logger.error("[FolderScanner] Error scanning %s: %s", ext, e)
                continue  # Skip extension yang error
        
        return files
    
    def get_all_db_file_paths(self, db: Session) -> Set[str]:
        """Get all file paths from database."""
        records = db.query(Snapshot.file_path).all()
        return set(r[0] for r in records if r[0])
    
    def scan_for_orphaned(self, db: Session) -> Tuple[List[Dict], int, int]:
        """Scan for orphaned files."""
        try:
            # Get files from disk
            disk_files = self.get_all_files_on_disk()
            total_disk = len(disk_files)
            logger.info("[FolderScanner] Total files on disk: %d", total_disk)
            
            # Get paths from DB
            try:
                db_paths = self.get_all_db_file_paths(db)
                total_db = len(db_paths)
                logger.info("[FolderScanner] Total records in DB: %d", total_db)
            except Exception as e:
                logger.error("[FolderScanner] Error getting DB paths: %s", e)
                raise Exception(f"Database error: {e}")
            
            # Find orphaned
            orphaned = []
            for file_path in disk_files:
                try:
                    # Convert absolute path to relative path
                    rel_path = file_path.relative_to(self.base_dir).as_posix()
                    
                    if rel_path not in db_paths:
                        # Try to extract camera_id from path
                        parts = rel_path.split('/')
                        camera_id = parts[0] if len(parts) > 1 else None
                        
                        # Get file stats dengan error handling
                        try:
                            stat = file_path.stat()
                            size = stat.st_size
                            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        except (OSError, IOError) as e:
                            logger.warning("[FolderScanner] Cannot stat file %s: %s", file_path, e)
                            size = 0
                            modified = None
                        
                        orphaned.append({
                            'absolute_path': str(file_path),
                            'relative_path': rel_path,
                            'camera_id': camera_id,
                            'size': size,
                            'modified': modified
                        })
                except ValueError as e:
                    logger.warning("[FolderScanner] Path error for %s: %s", file_path, e)
                    continue
                except Exception as e:
                    logger.error("[FolderScanner] Unexpected error processing %s: %s", file_path, e)
                    continue
            
            logger.info(
                "[FolderScanner] Found %d orphaned files",
                len(orphaned)
            )
            
            return orphaned, total_disk, total_db
            
        except Exception as e:
            logger.error("[FolderScanner] Error in scan_for_orphaned: %s", e)
            raise


    def sync_to_orphaned_table(self, db: Session) -> Tuple[int, int]:
        """Sync scan results to OrphanedFile table."""
        try:
            orphaned, _, _ = self.scan_for_orphaned(db)
            
            # Get existing paths
            try:
                existing_paths = set(
                    r[0] for r in db.query(OrphanedFile.file_path).all()
                )
            except Exception as e:
                logger.error("[FolderScanner] Error querying existing paths: %s", e)
                existing_paths = set()
            
            new_count = 0
            for item in orphaned:
                if item['relative_path'] not in existing_paths:
                    try:
                        orphaned_record = OrphanedFile(
                            file_path=item['relative_path'],
                            file_size=item['size'],
                            camera_id=item['camera_id'],
                            status='pending'
                        )
                        db.add(orphaned_record)
                        new_count += 1
                    except Exception as e:
                        logger.error("[FolderScanner] Error creating record for %s: %s", 
                                item['relative_path'], e)
                        continue
            
            # Commit dengan error handling
            try:
                db.commit()
                logger.info("[FolderScanner] Committed %d new records", new_count)
            except Exception as e:
                logger.error("[FolderScanner] Commit error: %s", e)
                db.rollback()
                raise Exception(f"Database commit failed: {e}")
            
            # Mark resolved records
            try:
                all_records = db.query(OrphanedFile).filter(
                    OrphanedFile.status.in_(['pending', 'reviewed'])
                ).all()
                
                current_disk_paths = {o['relative_path'] for o in orphaned}
                resolved_count = 0
                
                for record in all_records:
                    if record.file_path not in current_disk_paths:
                        record.status = 'resolved'
                        record.notes = 'File no longer exists on disk'
                        resolved_count += 1
                
                db.commit()
                logger.info("[FolderScanner] Marked %d records as resolved", resolved_count)
            except Exception as e:
                logger.error("[FolderScanner] Error marking resolved: %s", e)
                db.rollback()
                # Jangan raise, ini tidak critical
            
            # Get total count
            try:
                total_orphaned = db.query(OrphanedFile).filter(
                    OrphanedFile.status.in_(['pending', 'reviewed'])
                ).count()
            except Exception as e:
                logger.error("[FolderScanner] Error counting total: %s", e)
                total_orphaned = 0
            
            return new_count, total_orphaned
            
        except Exception as e:
            logger.error("[FolderScanner] Error in sync_to_orphaned_table: %s", e)
            db.rollback()
            raise

# ============================================================================
# PENDEKATAN 2: ORPHANED FILE MANAGER
# ============================================================================

class OrphanedFileManager:
    """Manager untuk tabel OrphanedFile.
    
    Menyediakan operasi CRUD dan cleanup untuk orphaned files.
    """
    
    def __init__(self, base_dir: str = SNAPSHOT_BASE_DIR):
        self.base_dir = Path(base_dir)
    
    def get_pending_files(self, db: Session, limit: int = None) -> List[OrphanedFile]:
        """Get pending orphaned files."""
        query = db.query(OrphanedFile).filter(
            OrphanedFile.status == 'pending'
        ).order_by(OrphanedFile.detected_at.desc())
        
        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    def get_files_by_camera(self, db: Session, camera_id: str) -> List[OrphanedFile]:
        """Get orphaned files for specific camera."""
        return db.query(OrphanedFile).filter(
            OrphanedFile.camera_id == camera_id
        ).order_by(OrphanedFile.detected_at.desc()).all()
    
    def get_storage_summary(self, db: Session) -> Dict:
        """Get summary of orphaned file storage."""
        from sqlalchemy import func, inspect  # ← import inspect di sini
        from sqlalchemy.exc import SQLAlchemyError
        
        try:
            # Check if table exists first (opsional tapi recommended)
            try:
                inspector = inspect(db.bind)
                if not inspector.has_table("orphaned_files"):
                    logger.warning("[OrphanedManager] Table 'orphaned_files' not found")
                    return {
                        'total_count': 0,
                        'total_size_bytes': 0,
                        'total_size_mb': 0,
                        'by_camera': []
                    }
            except Exception as e:
                logger.debug("[OrphanedManager] Could not inspect tables: %s", e)
                # Continue anyway, mungkin table ada tapi inspect gagal
            
            # Query for total count
            total_count = db.query(OrphanedFile).filter(
                OrphanedFile.status.in_(['pending', 'reviewed'])
            ).count()
            
            # Query for total size using SQLAlchemy func
            total_size_result = db.query(
                func.coalesce(func.sum(OrphanedFile.file_size), 0)
            ).filter(
                OrphanedFile.status.in_(['pending', 'reviewed'])
            ).scalar()
            
            total_size = total_size_result or 0
            
            # Query grouped by camera using SQLAlchemy func
            by_camera_results = db.query(
                OrphanedFile.camera_id,
                func.count().label('file_count'),
                func.coalesce(func.sum(OrphanedFile.file_size), 0).label('total_size')
            ).filter(
                OrphanedFile.status.in_(['pending', 'reviewed'])
            ).group_by(OrphanedFile.camera_id).all()
            
            return {
                'total_count': total_count or 0,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2) if total_size else 0,
                'by_camera': [
                    {
                        'camera_id': c.camera_id or 'unknown',
                        'count': c.file_count,
                        'size_mb': round(c.total_size / (1024 * 1024), 2) if c.total_size else 0
                    }
                    for c in by_camera_results
                ]
            }
        except SQLAlchemyError as e:
            logger.error("[OrphanedManager] Database error in get_storage_summary: %s", e)
            return {
                'total_count': 0,
                'total_size_bytes': 0,
                'total_size_mb': 0,
                'by_camera': []
            }   
    
    def _cleanup_empty_folders(self, file_path: Path) -> None:
        """Remove empty parent folders after file deletion.
        
        Folder structure: base_dir/<camera_id>/<date>/<filename>.jpg
        Removes <date> folder if empty, then <camera_id> folder if empty.
        """
        try:
            # Get parent folders
            date_folder = file_path.parent      # .../<camera_id>/<date>
            camera_folder = date_folder.parent  # .../<camera_id>
            
            # Remove empty date folder
            if date_folder.exists() and not any(date_folder.iterdir()):
                date_folder.rmdir()
                logger.info("[OrphanedManager] Removed empty date folder: %s", date_folder.name)
                
                # Remove empty camera folder
                if camera_folder.exists() and not any(camera_folder.iterdir()):
                    camera_folder.rmdir()
                    logger.info("[OrphanedManager] Removed empty camera folder: %s", camera_folder.name)
                    
        except OSError as e:
            logger.warning("[OrphanedManager] Could not remove empty folders: %s", e)
    
    def delete_file(self, db: Session, orphaned_id: int, delete_from_disk: bool = True) -> bool:
        """Delete orphaned file record and optionally from disk.
        
        Returns:
            True if successful
        """
        record = db.query(OrphanedFile).filter(OrphanedFile.id == orphaned_id).first()
        if not record:
            return False
        
        file_path = self.base_dir / record.file_path
        
        if delete_from_disk and file_path.exists():
            try:
                file_path.unlink()
                logger.info("[OrphanedManager] Deleted file from disk: %s", record.file_path)
                
                # Cleanup empty parent folders
                self._cleanup_empty_folders(file_path)
                
            except OSError as e:
                logger.error("[OrphanedManager] Failed to delete file: %s - %s", record.file_path, e)
                return False
        
        record.status = 'deleted'
        record.notes = f"Deleted from disk: {delete_from_disk}"
        db.commit()
        
        return True
    
    def delete_all_pending(self, db: Session, delete_from_disk: bool = True) -> Tuple[int, int]:
        """Delete all pending orphaned files.
        
        Returns:
            Tuple of (success_count, fail_count)
        """
        pending = self.get_pending_files(db)
        
        logger.info("[delete_all_pending] Found %d pending files to delete", len(pending))
        
        success = 0
        failed = 0
        
        for record in pending:
            file_path = self.base_dir / record.file_path
            logger.debug("[delete_all_pending] Processing: %s (exists: %s)", 
                        record.file_path, file_path.exists())
            
            if self.delete_file(db, record.id, delete_from_disk):
                success += 1
                logger.debug("[delete_all_pending] Success: %s", record.file_path)
            else:
                failed += 1
                logger.warning("[delete_all_pending] Failed: %s", record.file_path)
        
        logger.info("[delete_all_pending] Complete: %d success, %d failed", success, failed)
        return success, failed
    
    def review_file(self, db: Session, orphaned_id: int, notes: str = None) -> bool:
        """Mark file as reviewed."""
        record = db.query(OrphanedFile).filter(OrphanedFile.id == orphaned_id).first()
        if not record:
            return False
        
        record.status = 'reviewed'
        if notes:
            record.notes = notes
        
        db.commit()
        return True
    
    def restore_file(self, db: Session, orphaned_id: int) -> bool:
        """Mark file as restored (no longer orphaned)."""
        record = db.query(OrphanedFile).filter(OrphanedFile.id == orphaned_id).first()
        if not record:
            return False
        
        record.status = 'restored'
        record.notes = 'File restored or camera recreated'
        
        db.commit()
        return True


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def run_orphaned_scan(db: Session) -> Dict:
    """Run full orphaned file scan and sync to database.
    
    This is the main entry point for scheduled jobs.
    
    Returns:
        Summary dict with scan results
    """
    from sqlalchemy.exc import SQLAlchemyError
    
    try:
        scanner = FolderScanner()
        manager = OrphanedFileManager()
        
        # Scan folder
        orphaned_list, disk_count, db_count = scanner.scan_for_orphaned(db)
        
        # Sync to table
        new_count, total_orphaned = scanner.sync_to_orphaned_table(db)
        
        # Get storage summary
        summary = manager.get_storage_summary(db)
        
        return {
            'disk_files': disk_count,
            'db_records': db_count,
            'orphaned_found': len(orphaned_list),
            'new_records': new_count,
            'total_orphaned': total_orphaned,
            'storage_summary': summary
        }
    except SQLAlchemyError as e:
        logger.error("[run_orphaned_scan] Database error: %s", e)
        return {
            'disk_files': 0,
            'db_records': 0,
            'orphaned_found': 0,
            'new_records': 0,
            'total_orphaned': 0,
            'storage_summary': {
                'total_count': 0,
                'total_size_bytes': 0,
                'total_size_mb': 0,
                'by_camera': []
            }
        }


def cleanup_orphaned_files(db: Session, max_age_days: int = None, dry_run: bool = False) -> Dict:
    """Cleanup orphaned files older than specified days.
    
    Args:
        db: Database session
        max_age_days: Delete files older than this (None = all, 0 = all pending)
        dry_run: If True, only return what would be deleted
        
    Returns:
        Summary of cleanup operation
    """
    from sqlalchemy.exc import SQLAlchemyError
    
    try:
        manager = OrphanedFileManager()
        
        query = db.query(OrphanedFile).filter(
            OrphanedFile.status == 'pending'
        )
        
        # Jika max_age_days adalah 0 atau None, hapus SEMUA pending
        if max_age_days is not None and max_age_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            query = query.filter(OrphanedFile.detected_at < cutoff)
            logger.info("[cleanup] Filtering files older than %d days", max_age_days)
        else:
            logger.info("[cleanup] Will delete ALL pending files (no age filter)")
        
        to_delete = query.all()
        
        logger.info("[cleanup] Found %d files to delete", len(to_delete))
        
        if dry_run:
            total_size = sum(f.file_size or 0 for f in to_delete)
            logger.info("[cleanup] Dry run: would delete %d files", len(to_delete))
            return {
                'would_delete': len(to_delete),
                'would_free_bytes': total_size,
                'would_free_mb': round(total_size / (1024 * 1024), 2)
            }
        
        # Delete one by one untuk logging
        success = 0
        failed = 0
        for record in to_delete:
            if manager.delete_file(db, record.id, delete_from_disk=True):
                success += 1
                logger.debug("[cleanup] Deleted: %s", record.file_path)
            else:
                failed += 1
                logger.warning("[cleanup] Failed to delete: %s", record.file_path)
        
        logger.info("[cleanup] Complete: %d success, %d failed", success, failed)
        
        return {
            'deleted': success,
            'failed': failed,
            'total_processed': success + failed
        }
    except SQLAlchemyError as e:
        logger.error("[cleanup_orphaned_files] Database error: %s", e)
        if dry_run:
            return {
                'would_delete': 0,
                'would_free_bytes': 0,
                'would_free_mb': 0
            }
        else:
            return {
                'deleted': 0,
                'failed': 0,
                'total_processed': 0
            }


def cleanup_orphaned_files_from_disk(db: Session, dry_run: bool = False) -> Dict:
    """Cleanup orphaned files directly from disk (bypass table check).
    
    This function scans disk for files not in DB and deletes them directly.
    Useful when table sync failed or files haven't been scanned yet.
    
    Args:
        db: Database session
        dry_run: If True, only return what would be deleted
        
    Returns:
        Summary of cleanup operation
    """
    from sqlalchemy.exc import SQLAlchemyError
    
    try:
        scanner = FolderScanner()
        manager = OrphanedFileManager()
        
        # Scan for orphaned files
        orphaned_list, disk_count, db_count = scanner.scan_for_orphaned(db)
        
        logger.info("[cleanup_disk] Found %d orphaned files on disk", len(orphaned_list))
        
        if dry_run:
            total_size = sum(f.get('size', 0) for f in orphaned_list)
            return {
                'would_delete': len(orphaned_list),
                'would_free_bytes': total_size,
                'would_free_mb': round(total_size / (1024 * 1024), 2)
            }
        
        # Delete files directly from disk
        success = 0
        failed = 0
        total_size = 0
        
        for item in orphaned_list:
            file_path = manager.base_dir / item['relative_path']
            file_size = item.get('size', 0)
            
            try:
                if file_path.exists():
                    file_path.unlink()
                    total_size += file_size
                    success += 1
                    logger.info("[cleanup_disk] Deleted: %s", item['relative_path'])
                    
                    # Try to remove empty parent directories
                    try:
                        date_folder = file_path.parent      # .../<camera_id>/<date>
                        camera_folder = date_folder.parent  # .../<camera_id>
                        
                        # Remove empty date folder
                        if date_folder.exists() and not any(date_folder.iterdir()):
                            date_folder.rmdir()
                            logger.info("[cleanup_disk] Removed empty date folder: %s", date_folder.name)
                            
                            # Remove empty camera folder
                            if camera_folder.exists() and not any(camera_folder.iterdir()):
                                camera_folder.rmdir()
                                logger.info("[cleanup_disk] Removed empty camera folder: %s", camera_folder.name)
                                
                    except OSError as e:
                        logger.debug("[cleanup_disk] Could not remove empty folders: %s", e)
                    
                else:
                    logger.warning("[cleanup_disk] File not found: %s", item['relative_path'])
                    failed += 1
            except OSError as e:
                logger.error("[cleanup_disk] Failed to delete %s: %s", item['relative_path'], e)
                failed += 1
        
        # Also mark any matching records in table as deleted
        for item in orphaned_list:
            db.query(OrphanedFile).filter(
                OrphanedFile.file_path == item['relative_path']
            ).update({
                'status': 'deleted',
                'notes': 'Deleted via disk cleanup'
            })
        
        db.commit()
        
        logger.info("[cleanup_disk] Complete: %d success, %d failed, %d MB freed",
                   success, failed, round(total_size / (1024 * 1024), 2))
        
        return {
            'deleted': success,
            'failed': failed,
            'total_processed': success + failed,
            'bytes_freed': total_size,
            'mb_freed': round(total_size / (1024 * 1024), 2)
        }
        
    except SQLAlchemyError as e:
        logger.error("[cleanup_orphaned_files_from_disk] Database error: %s", e)
        return {
            'deleted': 0,
            'failed': 0,
            'total_processed': 0,
            'bytes_freed': 0,
            'mb_freed': 0
        }
        if dry_run:
            return {
                'would_delete': 0,
                'would_free_bytes': 0,
                'would_free_mb': 0
            }
        else:
            return {
                'deleted': 0,
                'failed': 0,
                'total_processed': 0
            }
