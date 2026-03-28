"""Append-Only Audit Log Model (P2-001).

This model represents an immutable audit log that cannot be modified or deleted.
Database triggers enforce append-only behavior at the database level.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, event, DDL
from app.db.database import Base

class AuditLog(Base):
    """Immutable audit log entry.
    
    P2-001: Append-only audit log with database-level enforcement.
    Records cannot be updated or deleted once created.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
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


# P2-001: Create database triggers to enforce append-only behavior
def create_audit_log_triggers(target, connection, **kw):
    """Create PostgreSQL triggers to prevent UPDATE and DELETE on audit_logs."""
    
    # Trigger to prevent DELETE
    prevent_delete_trigger = DDL("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be deleted. This is an append-only table.';
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
        COMMENT ON TABLE audit_logs IS 'Append-only audit log. Records cannot be updated or deleted.';
    """)
    
    connection.execute(prevent_delete_trigger)
    connection.execute(prevent_update_trigger)
    connection.execute(table_comment)


# Register the event listener
event.listen(AuditLog.__table__, 'after_create', create_audit_log_triggers)
