"""Audit Logger - P2-001: Append-Only Audit Log.

This module provides audit logging functionality with append-only guarantees.
Audit records cannot be modified or deleted once created (enforced at database level).
"""
import json
from app.models.audit_log import AuditLog
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Any, Optional

def log_audit(
    db: Session, 
    user: str, 
    action: str, 
    target: str, 
    ip: str = None, 
    extra: Any = None,
    user_agent: str = None,
    request_path: str = None,
    request_method: str = None,
    response_status: int = None,
):
    """Log an audit event to the append-only audit log.
    
    P2-001: Audit logs are append-only and cannot be modified or deleted.
    Database triggers enforce this at the PostgreSQL level.
    
    Args:
        db: Database session
        user: Username performing the action
        action: Type of action (e.g., 'create_camera', 'delete_snapshot')
        target: Target resource (e.g., camera hostname, snapshot ID)
        ip: Client IP address
        extra: Additional data (dict/list will be JSON serialized)
        user_agent: HTTP User-Agent header
        request_path: API endpoint or request path
        request_method: HTTP method (GET, POST, etc.)
        response_status: HTTP response status code
    """
    extra_str = None
    if extra:
        if isinstance(extra, (dict, list)):
            extra_str = json.dumps(extra)
        else:
            extra_str = str(extra)

    audit = AuditLog(
        timestamp=datetime.now(timezone.utc),
        user=user or 'anonymous',
        action=action,
        target=target,
        ip=ip,
        extra=extra_str,
        user_agent=user_agent,
        request_path=request_path,
        request_method=request_method,
        response_status=response_status,
    )
    db.add(audit)
    db.commit()


def log_api_access(
    db: Session,
    request,
    response_status: int = 200,
    user: str = None,
    action: str = 'api_access',
):
    """Log API access with full request details.
    
    P2-001: Enhanced audit logging for API endpoints.
    
    Args:
        db: Database session
        request: FastAPI/Starlette request object
        response_status: HTTP response status code
        user: Username (extracted from session if not provided)
        action: Action type for categorization
    """
    # Extract user from session if not provided
    if user is None and hasattr(request, 'session'):
        user = request.session.get('user_name', 'anonymous')
    
    user = user or 'anonymous'
    
    # Extract request details
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get('user-agent')
    path = str(request.url.path)
    method = request.method
    
    log_audit(
        db=db,
        user=user,
        action=action,
        target=path,
        ip=client_ip,
        user_agent=user_agent,
        request_path=path,
        request_method=method,
        response_status=response_status,
    )