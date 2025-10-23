from typing import Optional
from fastapi import Request
from fastapi.responses import HTMLResponse
from app.db.database import get_db
from app.utils.timezone_helper import get_current_timezone, format_datetime_with_tz
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.utils.template_helper import templates
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import joinedload, Session
from math import ceil
import pytz

router = APIRouter(tags=["Email Logs"])


@router.get("/email-logs", response_class=HTMLResponse)
def view_email_logs(
    request: Request,
    db: Session = Depends(get_db),
    camera_name: Optional[str] = Query(None),
    success: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    query = db.query(CameraEmailNotificationLog).options(
        joinedload(CameraEmailNotificationLog.recipients)
    )

    if camera_name:
        query = query.filter(CameraEmailNotificationLog.camera_name.ilike(f"%{camera_name}%"))

    success_bool = None
    if success is not None and success != "":
        if success.lower() in ["true", "1", "yes"]:
            success_bool = True
        elif success.lower() in ["false", "0", "no"]:
            success_bool = False

    if success_bool is not None:
        query = query.filter(CameraEmailNotificationLog.success == success_bool)

    total = query.count()
    total_pages = ceil(total / per_page)

    logs = (
        query.order_by(CameraEmailNotificationLog.sent_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    # === Ambil timezone sekali saja ===
    tz_name = get_current_timezone(db)
    local_tz = pytz.timezone(tz_name)

    for log in logs:
        # sent_at
        if log.sent_at:
            if log.sent_at.tzinfo is None:
                log.sent_at = pytz.utc.localize(log.sent_at)
            log.local_sent_at = format_datetime_with_tz(log.sent_at.astimezone(local_tz))
        else:
            log.local_sent_at = "N/A"

        # incident_started_at
        if log.incident_started_at:
            if log.incident_started_at.tzinfo is None:
                log.incident_started_at = pytz.utc.localize(log.incident_started_at)
            log.local_incident_started_at = format_datetime_with_tz(
                log.incident_started_at.astimezone(local_tz)
            )
        else:
            log.local_incident_started_at = "N/A"


    return templates.TemplateResponse(
        "email_logs.html",
        {
            "request": request,
            "logs": logs,
            "camera_name": camera_name,
            "success": success,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "reason": reason,
        },
    )
