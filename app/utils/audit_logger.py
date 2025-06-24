from app.models_sql import AuditLog
from sqlalchemy.orm import Session
from datetime import datetime, timezone

def log_audit(db: Session, user: str, action: str, target: str, ip: str = None, extra: str = None):
    audit = AuditLog(
        timestamp=datetime.now(timezone.utc),
        user=user,
        action=action,
        target=target,
        ip=ip,
        extra=extra,
    )
    db.add(audit)
    db.commit()