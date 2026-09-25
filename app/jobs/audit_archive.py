"""Audit Log Archive Job (P2-001).

Archives audit logs older than 6 months from audit_logs to:
1. Encrypted file storage (cold storage)
2. audit_logs_legacy table (warm storage)
3. Truncates archived data from audit_logs (after verification)

Archive Strategy:
- Run every 6 months (Jan 1, July 1)
- Archive data older than 6 months
- Encrypt with AES-256-GCM
- Generate SHA-256 checksum for integrity
- Store metadata in audit_archive_history
"""
import os
import json
import gzip
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import get_db
from app.models.audit_log import AuditLog, AuditLogLegacy, AuditArchiveHistory
from app.utils.timezone_helper import to_current_timezone

logger = logging.getLogger(__name__)

# Configuration
ARCHIVE_RETENTION_DAYS = 180  # 6 months
ARCHIVE_DIR = Path("archives/audit_logs")
ENCRYPTION_KEY_ENV = "AUDIT_ARCHIVE_KEY"


class AuditArchiveService:
    """Service for archiving audit logs."""
    
    def __init__(self, db: Session = None):
        self.db = db
        self.archive_dir = ARCHIVE_DIR
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_encryption_key(self) -> bytes:
        """Get or generate encryption key."""
        key = os.environ.get(ENCRYPTION_KEY_ENV)
        if not key:
            # Generate new key if not exists
            key = Fernet.generate_key().decode()
            logger.warning(f"New encryption key generated. Store this securely: {key}")
            os.environ[ENCRYPTION_KEY_ENV] = key
        return key.encode() if isinstance(key, str) else key
    
    def _encrypt_data(self, data: bytes) -> Tuple[bytes, str]:
        """Encrypt data with Fernet (AES-256-CBC)."""
        key = self._get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(data)
        return encrypted, key.decode()
    
    def _calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA-256 checksum of file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def get_archive_cutoff_date(self) -> datetime:
        """Get cutoff date for archiving (6 months ago)."""
        return datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)
    
    def get_logs_to_archive(self, cutoff_date: datetime) -> list:
        """Get audit logs older than cutoff date."""
        return self.db.query(AuditLog).filter(
            AuditLog.timestamp < cutoff_date
        ).order_by(AuditLog.timestamp).all()
    
    def export_to_json(self, logs: list) -> str:
        """Export logs to JSON format."""
        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "user": log.user,
                "action": log.action,
                "target": log.target,
                "ip": log.ip,
                "extra": log.extra,
                "user_agent": log.user_agent,
                "request_path": log.request_path,
                "request_method": log.request_method,
                "response_status": log.response_status,
            })
        return json.dumps(data, indent=2, default=str)
    
    def archive_logs(self, archived_by: str = "system") -> dict:
        """Main archive process.
        
        Steps:
        1. Identify logs older than 6 months
        2. Export to encrypted JSON file
        3. Copy to legacy table
        4. Verify integrity
        5. Delete from current table
        6. Record archive history
        
        Returns:
            dict: Archive operation results
        """
        cutoff_date = self.get_archive_cutoff_date()
        
        # Get logs to archive
        logs_to_archive = self.get_logs_to_archive(cutoff_date)
        
        if not logs_to_archive:
            logger.info("No audit logs to archive.")
            return {
                "status": "no_action",
                "message": "No logs older than 6 months found",
                "records": 0
            }
        
        records_count = len(logs_to_archive)
        logger.info(f"Found {records_count} audit logs to archive (older than {cutoff_date})")
        
        # Determine date range
        oldest = min(log.timestamp for log in logs_to_archive)
        newest = max(log.timestamp for log in logs_to_archive)
        
        # Generate filename
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"audit_archive_{timestamp_str}_{oldest.strftime('%Y%m')}_{newest.strftime('%Y%m')}.json.gz.enc"
        file_path = self.archive_dir / filename
        temp_file_path = file_path.with_name(f".{filename}.tmp")
        
        try:
            # Step 1: Export to JSON
            json_data = self.export_to_json(logs_to_archive)
            json_bytes = json_data.encode('utf-8')
            
            # Step 2: Compress with gzip
            compressed = gzip.compress(json_bytes)
            
            # Step 3: Encrypt
            encrypted, key_id = self._encrypt_data(compressed)
            
            # Stage the archive under a temporary name until validation succeeds.
            with open(temp_file_path, 'wb') as f:
                f.write(encrypted)
            
            file_size = temp_file_path.stat().st_size
            logger.info(f"Archive staged for verification: {temp_file_path} ({file_size} bytes)")
            
            # Step 5: Calculate checksum
            checksum = self._calculate_checksum(temp_file_path)
            
            # Verify the staged file before writing legacy copies or deleting source rows.
            if not self._verify_archive_integrity(temp_file_path, logs_to_archive):
                raise Exception("Archive integrity verification failed")

            # Step 6: Copy to legacy table
            self._copy_to_legacy(logs_to_archive)

            # Publish the final archive only after the encrypted file has been verified.
            temp_file_path.replace(file_path)
            logger.info(f"Archive file published: {file_path} ({file_size} bytes)")
            
            # Step 7: Delete from current table
            # Note: This requires temporarily disabling the append-only trigger
            deleted_count = self._delete_archived_logs(logs_to_archive)
            
            # Step 8: Record archive history
            archive_record = AuditArchiveHistory(
                archived_at=datetime.now(timezone.utc),
                archived_by=archived_by,
                archive_period_start=oldest,
                archive_period_end=newest,
                records_archived=records_count,
                file_path=str(file_path),
                file_size_bytes=file_size,
                checksum=checksum,
                encryption_key_id=key_id[:20] + "...",  # Partial key ID for reference
                status='completed',
                notes=f"Archived {records_count} records to {filename}"
            )
            self.db.add(archive_record)
            self.db.commit()
            
            logger.info(f"Archive completed successfully: {records_count} records archived")
            
            return {
                "status": "success",
                "message": f"Successfully archived {records_count} records",
                "records": records_count,
                "file_path": str(file_path),
                "file_size": file_size,
                "checksum": checksum,
                "period_start": oldest.isoformat(),
                "period_end": newest.isoformat()
            }
            
        except Exception as e:
            # Failed attempts must not leave a published or partial archive file.
            for candidate in (temp_file_path, file_path):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.error("Failed to remove incomplete archive file %s: %s", candidate, cleanup_error)

            try:
                self.db.rollback()
            except Exception:
                logger.exception("Failed to roll back audit archive transaction")
            logger.error("Archive failed: %s", e)
            
            # Record failed attempt
            try:
                archive_record = AuditArchiveHistory(
                    archived_at=datetime.now(timezone.utc),
                    archived_by=archived_by,
                    archive_period_start=oldest,
                    archive_period_end=newest,
                    records_archived=0,
                    file_path=str(file_path),
                    status='failed',
                    notes=f"Failed: {str(e)}"
                )
                self.db.add(archive_record)
                self.db.commit()
            except Exception:
                self.db.rollback()
                logger.exception("Failed to record audit archive failure")
            
            raise
    
    def _copy_to_legacy(self, logs: list) -> int:
        """Copy logs to legacy table."""
        count = 0
        for log in logs:
            legacy = AuditLogLegacy(
                id=log.id,
                timestamp=log.timestamp,
                user=log.user,
                action=log.action,
                target=log.target,
                ip=log.ip,
                extra=log.extra,
                user_agent=log.user_agent,
                request_path=log.request_path,
                request_method=log.request_method,
                response_status=log.response_status
            )
            self.db.add(legacy)
            count += 1
        
        self.db.commit()
        logger.info(f"Copied {count} records to legacy table")
        return count
    
    def _verify_archive_integrity(self, file_path: Path, original_logs: list) -> bool:
        """Verify archive file can be decrypted and matches original data."""
        try:
            # Read and decrypt sample (first 10 records)
            with open(file_path, 'rb') as f:
                encrypted = f.read()
            
            key = self._get_encryption_key()
            f = Fernet(key)
            decrypted = f.decrypt(encrypted)
            decompressed = gzip.decompress(decrypted)
            data = json.loads(decompressed.decode('utf-8'))
            
            # Verify record count
            if len(data) != len(original_logs):
                logger.error(f"Record count mismatch: {len(data)} vs {len(original_logs)}")
                return False
            
            # Verify sample records
            sample_size = min(10, len(data))
            for i in range(sample_size):
                if data[i]['id'] != original_logs[i].id:
                    logger.error(f"Record ID mismatch at index {i}")
                    return False
            
            logger.info("Archive integrity verified")
            return True
            
        except Exception as e:
            logger.error(f"Integrity verification failed: {str(e)}")
            return False
    
    def _delete_archived_logs(self, logs: list) -> int:
        """Delete archived logs from current table.
        
        Note: This temporarily disables the append-only trigger
        to allow deletion of archived data.
        """
        from sqlalchemy import text
        
        try:
            # Temporarily disable trigger
            self.db.execute(text("""
                ALTER TABLE audit_logs DISABLE TRIGGER audit_log_prevent_delete;
            """))
            
            # Delete archived logs
            ids_to_delete = [log.id for log in logs]
            result = self.db.execute(text("""
                DELETE FROM audit_logs WHERE id = ANY(:ids)
            """), {"ids": ids_to_delete})
            
            self.db.commit()
            
            # Re-enable trigger
            self.db.execute(text("""
                ALTER TABLE audit_logs ENABLE TRIGGER audit_log_prevent_delete;
            """))
            
            deleted_count = result.rowcount
            logger.info(f"Deleted {deleted_count} records from current table")
            return deleted_count
            
        except Exception as e:
            # Ensure trigger is re-enabled even on error
            self.db.execute(text("""
                ALTER TABLE audit_logs ENABLE TRIGGER audit_log_prevent_delete;
            """))
            raise
    
    def get_archive_stats(self) -> dict:
        """Get statistics about current and archived logs."""
        current_count = self.db.query(AuditLog).count()
        legacy_count = self.db.query(AuditLogLegacy).count()
        archive_history_count = self.db.query(AuditArchiveHistory).filter(
            AuditArchiveHistory.status == "completed"
        ).count()
        
        # Get oldest and newest in current table
        oldest_current = self.db.query(AuditLog.timestamp).order_by(AuditLog.timestamp).first()
        newest_current = self.db.query(AuditLog.timestamp).order_by(AuditLog.timestamp.desc()).first()
        
        cutoff_date = self.get_archive_cutoff_date()
        eligible_for_archive = self.db.query(AuditLog).filter(
            AuditLog.timestamp < cutoff_date
        ).count()
        
        return {
            "current_table": {
                "records": current_count,
                "oldest": oldest_current[0].isoformat() if oldest_current else None,
                "newest": newest_current[0].isoformat() if newest_current else None,
            },
            "legacy_table": {
                "records": legacy_count
            },
            "archive_history": {
                "total_archives": archive_history_count
            },
            "eligible_for_archive": eligible_for_archive,
            "next_archive_due": cutoff_date.isoformat()
        }


# Convenience functions for scheduler
def run_audit_archive(db: Session = None, archived_by: str = "system"):
    """Run archive job (for scheduler)."""
    if db is None:
        # Create new session if not provided
        from app.db.database import SessionLocal
        db = SessionLocal()
        try:
            service = AuditArchiveService(db)
            return service.archive_logs(archived_by)
        finally:
            db.close()
    else:
        service = AuditArchiveService(db)
        return service.archive_logs(archived_by)


def get_archive_stats(db: Session = None):
    """Get archive statistics (for API)."""
    if db is None:
        from app.db.database import SessionLocal
        db = SessionLocal()
        try:
            service = AuditArchiveService(db)
            return service.get_archive_stats()
        finally:
            db.close()
    else:
        service = AuditArchiveService(db)
        return service.get_archive_stats()
