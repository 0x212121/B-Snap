import json
from app.models_sql import AuditLog
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Any

def log_audit(db: Session, user: str, action: str, target: str, ip: str = None, extra: Any = None):
    """
    Logs an audit event.
    The 'extra' parameter can be a dictionary, list, or simple string.
    It will be converted to a JSON string only if it's a dict or list.
    """
    extra_str = None
    if extra:
        # Only use json.dumps for complex types like dicts or lists
        if isinstance(extra, (dict, list)):
            extra_str = json.dumps(extra)
        else:
            # For simple strings, use them as-is
            extra_str = str(extra)

    audit = AuditLog(
        timestamp=datetime.now(timezone.utc),
        user=user,
        action=action,
        target=target,
        ip=ip,
        extra=extra_str,
    )
    db.add(audit)
    db.commit()