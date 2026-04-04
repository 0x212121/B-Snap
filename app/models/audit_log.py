"""Append-Only Audit Log Model (P2-001).

This model represents an immutable audit log that cannot be modified or deleted.
Database triggers enforce append-only behavior at the database level.

Dual-Table Strategy:
- audit_logs: Current data (append-only), archive every 6 months
- audit_logs_legacy: Historical data (>6 months), can be archived to cold storage
- audit_logs_unified: View combining both tables for unified queries
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, BigInteger, event, DDL
from app.db.database import Base


class AuditLog(Base):
    """Immutable audit log entry (Current - P2-001).
    
    P2-001: Append-only audit log with database-level enforcement.
    Records cannot be updated or deleted once created.
    Data older than 6 months should be archived to legacy table.
    """
    __tablename__ = "audit_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), 
                       default=lambda: datetime.now(timezone.utc), 
                       nullable=False, index=True)
    user = Column(String(100), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    target = Column(String(200), nullable=True, index=True)
    ip = Column(String(45))  # IPv6 compatible
    extra = Column(Text)  # JSON or detailed info
    
    # P2-001: Additional fields for enhanced audit
    user_agent = Column(String(500))
    request_path = Column(String(500))
    request_method = Column(String(10))
    response_status = Column(Integer)
    
    def __repr__(self):
        return f"<AuditLog(id={self.id}, user={self.user}, action={self.action}, target={self.target})>"


class AuditLogLegacy(Base):
    """Legacy audit log entry (Historical > 6 months).
    
    This table stores historical audit logs that have been migrated
    from the main audit_logs table. Data in this table can be:
    - Queried through the unified view
    - Exported to cold storage
    - Truncated after successful archival
    
    Note: This table does NOT have append-only triggers, allowing
    for archival and cleanup operations.
    """
    __tablename__ = "audit_logs_legacy"

    id = Column(BigInteger, primary_key=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    user = Column(String(100), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    target = Column(String(200), nullable=True, index=True)
    ip = Column(String(45))
    extra = Column(Text)
    
    # P2-001: Additional fields
    user_agent = Column(String(500))
    request_path = Column(String(500))
    request_method = Column(String(10))
    response_status = Column(Integer)
    
    def __repr__(self):
        return f"<AuditLogLegacy(id={self.id}, user={self.user}, action={self.action})>"


class AuditArchiveHistory(Base):
    """Track archive operations for audit logs.
    
    Records when audit logs were archived, where they were stored,
    and verification checksums for integrity.
    """
    __tablename__ = "audit_archive_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    archived_at = Column(DateTime(timezone=True), 
                         default=lambda: datetime.now(timezone.utc),
                         nullable=False, index=True)
    archived_by = Column(String(100), nullable=False)
    archive_period_start = Column(DateTime(timezone=True), nullable=False)
    archive_period_end = Column(DateTime(timezone=True), nullable=False)
    records_archived = Column(BigInteger, nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size_bytes = Column(BigInteger)
    checksum = Column(String(64))  # SHA-256
    encryption_key_id = Column(String(100))
    status = Column(String(20), default='completed')  # completed, failed, pending
    notes = Column(Text)


# P2-001: Create database triggers to enforce append-only behavior
def create_audit_log_triggers(target, connection, **kw):
    """Create PostgreSQL triggers to prevent UPDATE and DELETE on audit_logs."""
    
    # Trigger to prevent DELETE
    prevent_delete_trigger = DDL("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be deleted. This is an append-only table. Archive period: 6 months.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        
        DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs;
        CREATE TRIGGER audit_log_prevent_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_delete();
    """)
    
    # Trigger to prevent UPDATE
    prevent_update_trigger = DDL("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be modified. This is an append-only table.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        
        DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs;
        CREATE TRIGGER audit_log_prevent_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_update();
    """)
    
    # Comment explaining the table
    table_comment = DDL("""
        COMMENT ON TABLE audit_logs IS 'P2-001: Append-only audit log (current). Archive to legacy every 6 months.';
    """)
    
    connection.execute(prevent_delete_trigger)
    connection.execute(prevent_update_trigger)
    connection.execute(table_comment)


# Register the event listener only for AuditLog (not Legacy)
event.listen(AuditLog.__table__, 'after_create', create_audit_log_triggers)
