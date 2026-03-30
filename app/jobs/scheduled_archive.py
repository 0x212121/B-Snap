"""Scheduled Audit Log Archive Job.

Runs every 6 months to archive old audit logs.
Can be triggered manually or via scheduler.
"""
import logging
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.jobs.audit_archive import AuditArchiveService

logger = logging.getLogger(__name__)


def scheduled_audit_archive():
    """Scheduled job to archive audit logs every 6 months.
    
    This function is designed to be called by APScheduler.
    It runs the archive process for logs older than 6 months.
    
    Schedule: 0 2 1 * * (2 AM on 1st of January and July)
    """
    db = SessionLocal()
    try:
        logger.info("Starting scheduled audit log archive job")
        
        service = AuditArchiveService(db)
        result = service.archive_logs(archived_by="scheduled_job")
        
        if result.get("status") == "success":
            logger.info(
                f"Scheduled archive completed: {result['records']} records archived, "
                f"file: {result.get('file_path')}"
            )
        elif result.get("status") == "no_action":
            logger.info("No logs to archive")
        else:
            logger.error(f"Scheduled archive failed: {result.get('message')}")
            
    except Exception as e:
        logger.error(f"Scheduled archive job failed: {str(e)}", exc_info=True)
    finally:
        db.close()


def check_archive_needed() -> dict:
    """Check if archive is needed and return stats.
    
    Returns:
        dict with recommendation and stats
    """
    db = SessionLocal()
    try:
        service = AuditArchiveService(db)
        stats = service.get_archive_stats()
        
        eligible = stats.get("eligible_for_archive", 0)
        
        return {
            "archive_recommended": eligible > 10000,  # Recommend if > 10k records
            "archive_urgent": eligible > 50000,  # Urgent if > 50k records
            "stats": stats
        }
    finally:
        db.close()


# For testing
if __name__ == "__main__":
    # Run archive immediately for testing
    scheduled_audit_archive()
