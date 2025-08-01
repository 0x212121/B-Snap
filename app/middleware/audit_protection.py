from sqlalchemy import event
from sqlalchemy.orm import Session
from models.audit_log import AuditLog

@event.listens_for(Session, "before_flush")
def prevent_auditlog_update_or_delete(session, flush_context, instances):
    for instance in session.deleted:
        if isinstance(instance, AuditLog):
            raise Exception("AuditLog entries cannot be deleted.")
    for instance in session.dirty:
        if isinstance(instance, AuditLog):
            raise Exception("AuditLog entries cannot be updated.")
