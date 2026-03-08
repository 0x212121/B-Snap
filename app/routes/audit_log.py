"""Audit log routes for B-Snap."""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

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
    return templates.TemplateResponse(
        "audit_logs.html",
        {"request": request},
    )


@router.get("/api")
async def get_audit_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_access_required),
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Get audit logs with filtering and pagination."""
    offset = (page - 1) * per_page
    
    # Build base query
    query = db.query(AuditLog)
    
    # Apply filters
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (AuditLog.user.ilike(search_filter)) |
            (AuditLog.target.ilike(search_filter)) |
            (AuditLog.ip.ilike(search_filter))
        )
    
    if action:
        query = query.filter(AuditLog.action.ilike(f"%{action}%"))
    
    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(AuditLog.timestamp >= start)
        except ValueError:
            pass
    
    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d")
            # Add one day to include the full end date
            end = end + timedelta(days=1)
            query = query.filter(AuditLog.timestamp < end)
        except ValueError:
            pass
    
    # Get total count for pagination
    total = query.count()
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    
    # Get paginated logs
    logs = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(per_page).all()
    
    # Calculate stats
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.query(AuditLog).filter(AuditLog.timestamp >= today_start).count()
    
    unique_users = db.query(AuditLog.user).distinct().count()
    
    # Get top action
    top_action_result = db.query(
        AuditLog.action, 
        func.count(AuditLog.id).label("count")
    ).group_by(AuditLog.action).order_by(func.count(AuditLog.id).desc()).first()
    top_action = top_action_result[0] if top_action_result else None
    
    return JSONResponse({
        "logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else None,
                "user": log.user,
                "action": log.action,
                "target": log.target,
                "ip": log.ip,
                "extra": log.extra,
            }
            for log in logs
        ],
        "total": total,
        "total_logs": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "stats": {
            "today": today_count,
            "unique_users": unique_users,
            "top_action": top_action,
        }
    })
