from typing import Optional
import io
import csv
from datetime import datetime, timedelta
from fastapi import Request, APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import joinedload, Session
from sqlalchemy import func
from math import ceil
import pytz

from app.db.database import get_db
from app.utils.timezone_helper import get_current_timezone, format_datetime_standard
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.utils.template_helper import templates
from app.routes.auth import admin_access_required
from app.models.user import User

router = APIRouter(tags=["Email Logs"])


@router.get("/email-logs", response_class=HTMLResponse)
def view_email_logs(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Render email logs page with modern UI."""
    tz_name = get_current_timezone(db)
    
    return templates.TemplateResponse(
        "email_logs.html",
        {
            "request": request,
            "timezone": tz_name,
        },
    )


@router.get("/api/email-logs", response_class=JSONResponse)
def get_email_logs_api(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
    camera_name: Optional[str] = Query(None),
    success: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """Get email logs with filtering, pagination and stats."""
    try:
        query = db.query(CameraEmailNotificationLog).options(
            joinedload(CameraEmailNotificationLog.recipients)
        )

        # Apply filters
        if camera_name:
            query = query.filter(CameraEmailNotificationLog.camera_name.ilike(f"%{camera_name}%"))

        success_bool = None
        if success:
            if success.lower() in ["true", "1", "yes"]:
                success_bool = True
            elif success.lower() in ["false", "0", "no"]:
                success_bool = False

        if success_bool is not None:
            query = query.filter(CameraEmailNotificationLog.success == success_bool)
        
        if reason:
            query = query.filter(CameraEmailNotificationLog.reason.ilike(f"%{reason}%"))
        
        if start_date:
            query = query.filter(CameraEmailNotificationLog.sent_at >= start_date)
        
        if end_date:
            end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
            query = query.filter(CameraEmailNotificationLog.sent_at < end_date_dt)

        total = query.count()
        total_pages = ceil(total / per_page) if total > 0 else 1

        logs = (
            query.order_by(CameraEmailNotificationLog.sent_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Format logs using standardized datetime formatter
        logs_data = []
        for log in logs:
            # Format timestamps using helper that handles timezone conversion
            local_sent_at = format_datetime_standard(log.sent_at, db=db)
            local_incident_started_at = format_datetime_standard(log.incident_started_at, db=db)

            logs_data.append({
                "id": log.id,
                "camera_name": log.camera_name or "-",
                "incident_started_at": local_incident_started_at,
                "sent_at": local_sent_at,
                "recipients": [r.recipient_email for r in log.recipients] if log.recipients else [],
                "reason": log.reason or "-",
                "success": log.success,
                "error_message": log.error_message or None,
                "type": log.type or "alert",
            })

        # Calculate stats
        total_all = db.query(CameraEmailNotificationLog).count()
        success_count = db.query(CameraEmailNotificationLog).filter(
            CameraEmailNotificationLog.success == True
        ).count()
        failed_count = total_all - success_count
        success_rate = round((success_count / total_all * 100), 1) if total_all > 0 else 0
        
        # Today's count
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = db.query(CameraEmailNotificationLog).filter(
            CameraEmailNotificationLog.sent_at >= today_start
        ).count()

        # Get timezone for response
        tz_name = get_current_timezone(db)

        return {
            "logs": logs_data,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "timezone": tz_name,
            "stats": {
                "total": total_all,
                "success": success_count,
                "failed": failed_count,
                "success_rate": success_rate,
                "today": today_count,
            }
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Error in get_email_logs_api: %s", e)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "message": str(e)}
        )


@router.get("/api/email-logs/export")
def export_email_logs_csv(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
    camera_name: Optional[str] = Query(None),
    success: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Export email logs to CSV."""
    try:
        query = db.query(CameraEmailNotificationLog).options(
            joinedload(CameraEmailNotificationLog.recipients)
        )

        # Apply same filters as API
        if camera_name:
            query = query.filter(CameraEmailNotificationLog.camera_name.ilike(f"%{camera_name}%"))

        success_bool = None
        if success:
            if success.lower() in ["true", "1", "yes"]:
                success_bool = True
            elif success.lower() in ["false", "0", "no"]:
                success_bool = False

        if success_bool is not None:
            query = query.filter(CameraEmailNotificationLog.success == success_bool)
        
        if reason:
            query = query.filter(CameraEmailNotificationLog.reason.ilike(f"%{reason}%"))
        
        if start_date:
            query = query.filter(CameraEmailNotificationLog.sent_at >= start_date)
        
        if end_date:
            end_date_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
            query = query.filter(CameraEmailNotificationLog.sent_at < end_date_dt)

        logs = query.order_by(CameraEmailNotificationLog.sent_at.desc()).all()

        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Camera Name", "Incident Started", "Sent At", "Recipients", 
            "Type", "Reason", "Status", "Error Message"
        ])

        for log in logs:
            # Format timestamps using standardized helper
            sent_at_str = format_datetime_standard(log.sent_at, db=db)
            incident_str = format_datetime_standard(log.incident_started_at, db=db)

            recipients = ", ".join([r.recipient_email for r in log.recipients]) if log.recipients else "-"
            
            writer.writerow([
                log.camera_name or "-",
                incident_str,
                sent_at_str,
                recipients,
                log.type or "alert",
                log.reason or "-",
                "Success" if log.success else "Failed",
                log.error_message or "-",
            ])

        csv_content = output.getvalue()
        output.close()
        
        return StreamingResponse(
            io.BytesIO(csv_content.encode('utf-8')),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=email_logs_export.csv"}
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Error exporting email logs: %s", e)
        return JSONResponse(
            status_code=500,
            content={"error": "Export failed", "message": str(e)}
        )
