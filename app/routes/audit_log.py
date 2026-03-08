"""Audit log routes for B-Snap."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import get_db
from app.models.audit_log import AuditLog
from app.routes.auth import admin_access_required
from app.models.user import User
from app.utils.template_helper import templates

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])


@router.get("/")
async def audit_logs_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    page: int = 1,
    per_page: int = 50,
):
    """Render audit logs page."""
    offset = (page - 1) * per_page
    
    logs = (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .offset(offset)
        .limit(per_page)
        .all()
    )
    
    total = db.query(AuditLog).count()
    
    return templates.TemplateResponse(
        "audit_logs.html",
        {
            "request": request,
            "logs": logs,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
        },
    )


@router.get("/api")
async def get_audit_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    limit: int = 50,
    offset: int = 0,
    user: Optional[str] = None,
    action: Optional[str] = None,
):
    """Get audit logs via API."""
    query = db.query(AuditLog)
    
    if user:
        query = query.filter(AuditLog.user.ilike(f"%{user}%"))
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    
    logs = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()
    
    return {
        "logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "user": log.user,
                "action": log.action,
                "target": log.target,
                "ip": log.ip,
                "extra": log.extra,
            }
            for log in logs
        ],
        "count": len(logs),
    }
