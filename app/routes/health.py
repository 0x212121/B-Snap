from collections import Counter, defaultdict
from datetime import datetime, timedelta, date, timezone
import logging
import math
from typing import Optional, Literal, List
from io import BytesIO
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
import pytz
from sqlalchemy import asc, desc, func, union_all, literal_column, select, and_
from sqlalchemy.orm import Session, joinedload
from app.core.config import get_config
from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.routes.auth import operator_access_required
from app.utils.healthcheck import run_healthcheck_for_all, run_healthcheck_for_camera, run_healthcheck_for_nvr
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Form, HTTPException
from app.models.nvr import NVR
from app.models.camera_daily_stats import CameraDailyStats
from app.models.health import CameraHealth as Health
from app.models.camera import Camera as DBCamera
from app.models.camera_group import CameraGroup
from app.models.health_check_status import HealthCheckStatus
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.user import User
from app.models.sla_report import SLAReport, ScheduledReport
from app.utils.template_helper import templates
from app.utils.timezone_helper import get_current_timezone, to_current_timezone

# --- ReportLab for PDF Generation ---
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.lib.utils import ImageReader

router = APIRouter(tags=["Health Check"])

logger = logging.getLogger("healthcheck")

class DeviceHealthStatus(BaseModel):
    id: str
    hostname: str
    ip: str
    dev_status: str | None
    type: str
    status: str | None
    latency: float | None
    checked_at: str | None = Field(None, alias="checked")
    last_online_at: str | None = Field(None, alias="last_online")
    status_changed_at: str | None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

class HealthStatusResponse(BaseModel):
    statuses: list[DeviceHealthStatus]
    camera_online_count: int
    nvr_online_count: int
    camera_offline_count: int
    nvr_offline_count: int

def _get_all_devices_health_data(db: Session) -> list:
    camera_q = select(
        DBCamera.id, DBCamera.hostname, DBCamera.ip,
        DBCamera.status.label("dev_status"),
        literal_column("'Camera'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, DBCamera.id == Health.camera_id).where(
        and_(
            DBCamera.ip.isnot(None),
            DBCamera.ip != ''
        )
    )

    nvr_q = select(
        NVR.id, NVR.hostname, NVR.ip,
        NVR.status.label("dev_status"),
        literal_column("'NVR'").label("type"),
        Health.status, Health.latency, Health.checked,
        Health.last_online, Health.status_changed_at
    ).join(Health, NVR.id == Health.nvr_id)

    unified_construct = union_all(camera_q, nvr_q).alias("unified_health")
    final_query = select(unified_construct).order_by(asc(unified_construct.c.hostname))
    return db.execute(final_query).all()

@router.get("/health", response_class=HTMLResponse)
async def health_monitor_page(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    
    logger.info(f"Current Timezone: {get_current_timezone(db)}")
    tz_name = get_current_timezone(db)
    return templates.TemplateResponse("health.html", {
        "request": request,
        "initial_data": HealthStatusResponse(
            statuses=[], camera_online_count=0, nvr_online_count=0,
            camera_offline_count=0, nvr_offline_count=0
        ).json(),
        "server_timezone": get_current_timezone(db),
        "server_timezone_label": pytz.timezone(tz_name).tzname(datetime.now())
    })

@router.get("/health/status", response_model=HealthStatusResponse)
async def get_health_status_api(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    try:
        all_devices = _get_all_devices_health_data(db)
        counts = Counter((dev.type, dev.status) for dev in all_devices)

        # Count online/offline
        camera_online = counts.get(("Camera", "Online"), 0) + counts.get(("Camera", "High Latency"), 0)
        nvr_online = counts.get(("NVR", "Online"), 0) + counts.get(("NVR", "High Latency"), 0)
        camera_offline = counts.get(("Camera", "Offline"), 0)
        nvr_offline = counts.get(("NVR", "Offline"), 0)

        formatted_devices = []
        for dev in all_devices:
            dev_dict = dict(dev._mapping)
            # Convert timestamps to ISO with timezone
            for ts_field in ["checked", "last_online", "status_changed_at"]:
                if dev_dict.get(ts_field):
                    # Biarkan format ISO (dengan offset) agar JS bisa parse dengan akurat
                    dev_dict[ts_field] = to_current_timezone(dev_dict[ts_field], db).isoformat()
                else:
                    dev_dict[ts_field] = None
            formatted_devices.append(dev_dict)

        # NEW: Inject server timezone (e.g. Asia/Makassar) dan nama label
        tz_name = get_current_timezone(db)
        return {
            "statuses": formatted_devices,
            "camera_online_count": camera_online,
            "nvr_online_count": nvr_online,
            "camera_offline_count": camera_offline,
            "nvr_offline_count": nvr_offline,
            "server_timezone": tz_name,
            "server_timezone_label": pytz.timezone(tz_name).tzname(datetime.now())
        }

    except Exception as e:
        print("Error in get_health_status_api: %s" % e)
        return JSONResponse(status_code=500, content={"message": "An internal error occurred."})


@router.post("/health/trigger/{entity_id}")
async def trigger_healthcheck(
    entity_id: str, background_tasks: BackgroundTasks,
    type: str = Query(..., enum=["camera", "nvr"]),
    current_operator: User = Depends(operator_access_required)
):
    if type == "camera":
        background_tasks.add_task(run_healthcheck_for_camera, entity_id)
    elif type == "nvr":
        background_tasks.add_task(run_healthcheck_for_nvr, entity_id)
    return {"status": f"Healthcheck triggered for {type.upper()} ID {entity_id}"}


@router.post("/health/trigger_all")
async def trigger_healthcheck_all(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    status = db.query(HealthCheckStatus).get(1)
    if not status:
        status = HealthCheckStatus(id=1)
        db.add(status)

    total_devices = db.query(DBCamera).count() + db.query(NVR).count()
    status.is_running = True
    status.start_time = datetime.now(timezone.utc)
    status.total_cameras = total_devices
    status.completed_cameras = 0
    db.commit()

    background_tasks.add_task(run_healthcheck_for_all)
    return {"status": "Healthcheck triggered for all devices"}

@router.get("/health/status/check")
async def check_healthcheck_status(db: Session = Depends(get_db), current_operator: User = Depends(operator_access_required)):
    status = db.query(HealthCheckStatus).get(1)
    if not status or not status.is_running or status.completed_cameras >= status.total_cameras:
        if status and status.is_running:
            status.is_running = False
            db.commit()
        return {"is_complete": True}

    return {
        "is_complete": False,
        "progress": {
            "total": status.total_cameras,
            "completed": status.completed_cameras
        }
    }


def format_duration(seconds: int) -> str:
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    elif seconds >= 60:
        return f"{seconds // 60}m"
    else:
        return f"{seconds}s"
    

@router.get("/health/history", response_class=HTMLResponse, name="health_history")
async def health_history(
    request: Request,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required),
    q: Optional[str] = "",
    sort: str = "name_asc",
    page: int = Query(1, ge=1)
):
    ITEMS_PER_PAGE = get_config("items_per_page", default=25)
    # Use configured timezone for date calculation
    tz_name = get_current_timezone(db)
    tz = pytz.timezone(tz_name)
    thirty_days_ago = datetime.now(tz).date() - timedelta(days=30)

    # Build subquery untuk aggregate stats
    stats_subq = (
        db.query(
            CameraDailyStats.camera_id,
            func.avg(CameraDailyStats.uptime_percentage).label("average_uptime")
        )
        .filter(CameraDailyStats.date >= thirty_days_ago)
        .group_by(CameraDailyStats.camera_id)
        .subquery()
    )

    # Query utama menggunakan subquery (lebih efisien)
    base_query = db.query(
        DBCamera.id,
        DBCamera.hostname,
        func.coalesce(stats_subq.c.average_uptime, 0).label("average_uptime")
    ).outerjoin(
        stats_subq, DBCamera.id == stats_subq.c.camera_id
    )

    if q:
        base_query = base_query.filter(DBCamera.hostname.ilike(f"%{q}%"))

    # Sorting yang benar - gunakan label, bukan string
    avg_uptime_col = func.coalesce(stats_subq.c.average_uptime, 0)
    
    if sort == "name_asc":
        base_query = base_query.order_by(asc(DBCamera.hostname))
    elif sort == "name_desc":
        base_query = base_query.order_by(desc(DBCamera.hostname))
    elif sort == "uptime_asc":
        base_query = base_query.order_by(asc(avg_uptime_col).nulls_last())
    elif sort == "uptime_desc":
        base_query = base_query.order_by(desc(avg_uptime_col).nulls_last())
    else:
        base_query = base_query.order_by(asc(DBCamera.hostname))

    # Pagination - count dengan subquery untuk akurasi
    count_query = db.query(func.count(DBCamera.id)).select_from(DBCamera)
    if q:
        count_query = count_query.filter(DBCamera.hostname.ilike(f"%{q}%"))
    
    total_items = count_query.scalar()
    total_pages = max(1, math.ceil(total_items / ITEMS_PER_PAGE))
    
    # Clamp page ke valid range
    page = min(page, total_pages) if total_pages > 0 else 1
    
    offset = (page - 1) * ITEMS_PER_PAGE
    
    # Ambil data dengan limit/offset
    paginated_results = base_query.limit(ITEMS_PER_PAGE).offset(offset).all()
    
    if not paginated_results:
        camera_ids_on_page = []
        full_camera_details = {}
    else:
        camera_ids_on_page = [item.id for item in paginated_results]

        # Load kamera + daily_stats dengan eager loading
        query_details = db.query(DBCamera).options(
            joinedload(DBCamera.daily_stats)
        ).filter(DBCamera.id.in_(camera_ids_on_page)).all()
        full_camera_details = {cam.id: cam for cam in query_details}

        # Load offline logs
        offline_logs = db.query(CameraStatusChangeLog).filter(
            CameraStatusChangeLog.camera_id.in_(camera_ids_on_page),
            CameraStatusChangeLog.previous_status == "Offline",
            CameraStatusChangeLog.changed_at != None,
            CameraStatusChangeLog.changed_at >= thirty_days_ago
        ).order_by(CameraStatusChangeLog.changed_at).all()

        # Group logs per camera + date
        offline_map = defaultdict(list)
        for log in offline_logs:
            try:
                local_dt = to_current_timezone(log.changed_at, db)
                date_str = local_dt.date().isoformat()
                time_str = local_dt.strftime("%H:%M %z")
                duration_secs = log.duration_since_last_change or 0
                duration_text = f"({format_duration(duration_secs)})" if duration_secs > 0 else ""
                offline_map[(log.camera_id, date_str)].append({
                    "time": time_str,
                    "duration_since_last_change": duration_secs,
                    "duration_text": duration_text
                })
            except Exception as e:
                logger.warning(f"Skipping log {log.id} due to error: {e}")

    # Build historical_data
    historical_data = []
    for item in paginated_results:
        cam = full_camera_details.get(item.id)
        if not cam:
            continue

        sorted_stats = sorted(
            cam.daily_stats, 
            key=lambda x: x.date if x.date else date.min, 
            reverse=True
        ) if cam.daily_stats else []

        stats_list = []
        for stat in sorted_stats:
            date_iso = stat.date.isoformat() if stat.date else None
            stats_list.append({
                "date": date_iso,
                "uptime_seconds": stat.total_uptime_seconds,
                "downtime_seconds": stat.total_downtime_seconds,
                "uptime_percentage": stat.uptime_percentage,
                "offline_times": offline_map.get((cam.id, date_iso), [])
            })

        historical_data.append({
            "hostname": getattr(cam, "hostname", f"Camera {cam.id}"),
            "average_uptime": float(item.average_uptime) if item.average_uptime else 0.0,
            "stats": stats_list
        })

    # Pagination data yang benar
    start_item = offset + 1 if paginated_results else 0
    end_item = min(offset + ITEMS_PER_PAGE, total_items) if paginated_results else 0

    pagination_data = {
        "page": page,
        "per_page": ITEMS_PER_PAGE,
        "total": total_items,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "prev_num": page - 1,
        "has_next": page < total_pages,
        "next_num": page + 1,
        "start_item": start_item,
        "end_item": end_item,
    }

    # Get all camera groups for the export modal
    camera_groups = db.query(CameraGroup).order_by(CameraGroup.name).all()
    
    return templates.TemplateResponse("health_history.html", {
        "request": request,
        "historical_data": historical_data,
        "pagination": pagination_data,
        "search_query": q,
        "current_sort": sort,
        "camera_groups": camera_groups
    })

# =============================================================================
# P0 FEATURES: Executive PDF Report Generator & SLA Compliance Dashboard
# =============================================================================

def _calculate_mttr_mtbf(db: Session, camera_id: str, start_date: date, end_date: date) -> tuple:
    """Calculate MTTR (Mean Time To Recovery) and MTBF (Mean Time Between Failures)."""
    logs = db.query(CameraStatusChangeLog).filter(
        CameraStatusChangeLog.camera_id == camera_id,
        CameraStatusChangeLog.changed_at >= start_date,
        CameraStatusChangeLog.changed_at <= datetime.combine(end_date, datetime.max.time())
    ).order_by(CameraStatusChangeLog.changed_at).all()
    
    if not logs:
        return None, None
    
    # Calculate MTTR: average duration of offline periods
    offline_durations = []
    online_durations = []
    
    for i, log in enumerate(logs):
        if log.previous_status == "Offline" and log.duration_since_last_change:
            offline_durations.append(log.duration_since_last_change)
        
        # Calculate time between failures (Offline -> Online -> Offline)
        if i > 0 and log.previous_status == "Online":
            prev_log = logs[i-1]
            if prev_log.previous_status == "Offline":
                time_between = (log.changed_at - prev_log.changed_at).total_seconds()
                online_durations.append(time_between)
    
    mttr = sum(offline_durations) / len(offline_durations) if offline_durations else None
    mtbf = sum(online_durations) / len(online_durations) if online_durations else None
    
    return mttr, mtbf


def _classify_incident_severity(duration_seconds: int) -> str:
    """Classify incident severity based on downtime duration."""
    if duration_seconds >= 3600:  # >= 1 hour
        return "critical"
    elif duration_seconds >= 600:  # >= 10 minutes
        return "major"
    else:
        return "minor"


def _get_sla_compliance_status(uptime_percentage: float, sla_threshold: float) -> str:
    """Determine compliance status based on uptime percentage and SLA threshold."""
    if uptime_percentage >= sla_threshold:
        return "Pass"
    elif uptime_percentage >= sla_threshold - 1.0:  # Within 1% of threshold
        return "Warning"
    else:
        return "Fail"


@router.get("/health/sla-report")
async def sla_report(
    request: Request,
    start_date: date,
    end_date: date,
    group_by: Literal["camera", "location", "group"] = "camera",
    sla_threshold: float = 99.5,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """
    Generate SLA (Service Level Agreement) compliance report.
    
    Returns MTTR, MTBF, incident severity classification, and compliance status.
    """
    # Ensure end_date includes the full day
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all cameras with daily stats in the period
    cameras = db.query(DBCamera).options(
        joinedload(DBCamera.daily_stats),
        joinedload(DBCamera.groups)
    ).all()
    
    sla_data = []
    total_incidents = {"critical": 0, "major": 0, "minor": 0}
    compliance_summary = {"pass": 0, "fail": 0, "warning": 0}
    
    for camera in cameras:
        # Filter daily stats within period
        period_stats = [
            stat for stat in camera.daily_stats
            if stat.date and start_date <= stat.date <= end_date
        ]
        
        if not period_stats:
            continue
        
        # Calculate aggregate metrics
        total_uptime = sum(s.total_uptime_seconds or 0 for s in period_stats)
        total_downtime = sum(s.total_downtime_seconds or 0 for s in period_stats)
        total_time = total_uptime + total_downtime
        
        uptime_percentage = (total_uptime / total_time * 100) if total_time > 0 else 100.0
        
        # Calculate MTTR and MTBF
        mttr, mtbf = _calculate_mttr_mtbf(db, camera.id, start_date, end_date)
        
        # Get incident logs for severity classification
        incident_logs = db.query(CameraStatusChangeLog).filter(
            CameraStatusChangeLog.camera_id == camera.id,
            CameraStatusChangeLog.previous_status == "Offline",
            CameraStatusChangeLog.changed_at >= start_date,
            CameraStatusChangeLog.changed_at <= end_datetime
        ).all()
        
        severity_counts = {"critical": 0, "major": 0, "minor": 0}
        for log in incident_logs:
            if log.duration_since_last_change:
                severity = _classify_incident_severity(log.duration_since_last_change)
                severity_counts[severity] += 1
                total_incidents[severity] += 1
        
        # Determine compliance status
        compliance_status = _get_sla_compliance_status(uptime_percentage, sla_threshold)
        compliance_summary[compliance_status.lower()] += 1
        
        sla_data.append({
            "camera_id": camera.id,
            "camera_name": camera.hostname,
            "location": camera.location,
            "group_name": ", ".join(group.name for group in camera.groups) or "Ungrouped",
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "uptime_percentage": round(uptime_percentage, 2),
            "total_uptime_seconds": total_uptime,
            "total_downtime_seconds": total_downtime,
            "mttr_seconds": round(mttr, 0) if mttr else None,
            "mtbf_seconds": round(mtbf, 0) if mtbf else None,
            "incident_count": len(incident_logs),
            "severity_critical": severity_counts["critical"],
            "severity_major": severity_counts["major"],
            "severity_minor": severity_counts["minor"],
            "compliance_status": compliance_status,
            "sla_threshold": sla_threshold
        })
    
    # Sort by uptime percentage (worst first)
    sla_data.sort(key=lambda x: x["uptime_percentage"])
    
    # Calculate summary statistics
    if sla_data:
        avg_uptime = sum(d["uptime_percentage"] for d in sla_data) / len(sla_data)
        worst_performer = sla_data[0]
        best_performer = max(sla_data, key=lambda x: x["uptime_percentage"])
    else:
        avg_uptime = 100.0
        worst_performer = None
        best_performer = None
    
    summary = {
        "total_cameras": len(sla_data),
        "average_uptime": round(avg_uptime, 2),
        "sla_threshold": sla_threshold,
        "compliance": compliance_summary,
        "total_incidents": total_incidents,
        "worst_performer": worst_performer,
        "best_performer": best_performer,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat()
    }
    
    # Check if JSON response requested
    if request.headers.get("accept") == "application/json":
        return {"summary": summary, "cameras": sla_data}
    
    # Render HTML template
    return templates.TemplateResponse("sla_report.html", {
        "request": request,
        "summary": summary,
        "sla_data": sla_data,
        "sla_threshold": sla_threshold,
        "start_date": start_date,
        "end_date": end_date
    })


@router.get("/health/history/export")
async def export_health_history(
    format: Literal["csv", "excel"] = "csv",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """
    Export health history data to CSV or Excel format.
    
    Quick Win (Q1): Export CSV/Excel dari Health History
    """
    # Default to last 30 days if no dates specified
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)
    
    try:
        # Query camera stats with group relationship loaded
        cameras = db.query(DBCamera).options(
            joinedload(DBCamera.daily_stats),
            joinedload(DBCamera.groups)
        ).all()
        
        # Prepare data
        rows = []
        for camera in cameras:
            period_stats = [
                stat for stat in camera.daily_stats
                if stat.date and start_date <= stat.date <= end_date
            ]
            
            if not period_stats:
                continue
            
            total_uptime = sum(s.total_uptime_seconds or 0 for s in period_stats)
            total_downtime = sum(s.total_downtime_seconds or 0 for s in period_stats)
            total_time = total_uptime + total_downtime
            uptime_percentage = (total_uptime / total_time * 100) if total_time > 0 else 100.0
            
            rows.append({
                "Camera Name": camera.hostname,
                "Location": camera.location or "",
                "Group": ", ".join(group.name for group in camera.groups) or "Ungrouped",
                "Period Start": start_date.isoformat(),
                "Period End": end_date.isoformat(),
                "Uptime %": round(uptime_percentage, 2),
                "Total Uptime (hours)": round(total_uptime / 3600, 2),
                "Total Downtime (hours)": round(total_downtime / 3600, 2),
                "Days with Data": len(period_stats)
            })
        
        # Sort by uptime percentage
        rows.sort(key=lambda x: x["Uptime %"])
        
        # Define headers explicitly
        headers = [
            "Camera Name", "Location", "Group", "Period Start", "Period End",
            "Uptime %", "Total Uptime (hours)", "Total Downtime (hours)", "Days with Data"
        ]
        
        if format == "csv":
            import csv
            from io import StringIO
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
            csv_content = output.getvalue()
            output.close()
            
            filename = f"health-history_{start_date}_{end_date}.csv"
            return StreamingResponse(
                BytesIO(csv_content.encode('utf-8')),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'}
            )
        
        else:  # excel
            try:
                import pandas as pd
                if rows:
                    df = pd.DataFrame(rows)
                else:
                    df = pd.DataFrame(columns=headers)
                output = BytesIO()
                df.to_excel(output, index=False, sheet_name="Health History")
                output.seek(0)
                
                filename = f"health-history_{start_date}_{end_date}.xlsx"
                return StreamingResponse(
                    output,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={filename}"}
                )
            except ImportError:
                # Fallback to CSV if pandas not available
                import csv
                from io import StringIO
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
                csv_content = output.getvalue()
                output.close()
                
                filename = f"health-history_{start_date}_{end_date}.csv"
                return StreamingResponse(
                    BytesIO(csv_content.encode('utf-8')),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )
    except Exception as e:
        logger.error(f"Export health history failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")


@router.post("/health/history/report")
async def generate_health_report(
    request: Request,
    start_date: date = Form(...),
    end_date: date = Form(...),
    format: Literal["pdf", "excel", "csv"] = Form("pdf"),
    include_charts: bool = Form(True),
    recipients: Optional[List[str]] = Form(None),
    sla_threshold: float = Form(99.5),
    groups: Optional[List[str]] = Form(None),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """
    Generate executive health report in PDF, Excel, or CSV format.
    
    Features:
    - Cover page with logo & period
    - Executive summary (total uptime, biggest incident, SLA compliance)
    - Charts: uptime trend, outage heatmap, top worst performers
    - Detail table per device with sorting
    - SLA compliance summary with Pass/Fail status
    - Filter by device groups
    """
    # Get form data for chart images (optional, not in form fields)
    form_data = await request.form()
    uptime_chart = form_data.get("uptime_chart")
    heatmap_chart = form_data.get("heatmap_chart")
    
    # Ensure end_date includes the full day
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get cameras with optional group filter
    cameras_query = db.query(DBCamera).options(
        joinedload(DBCamera.daily_stats),
        joinedload(DBCamera.groups)
    )
    
    # Filter by groups if specified
    if groups and len(groups) > 0:
        cameras_query = cameras_query.join(DBCamera.groups).filter(
            CameraGroup.name.in_(groups)
        )
    
    cameras = cameras_query.distinct().all()
    
    # Calculate metrics for each camera
    report_data = []
    total_incidents = {"critical": 0, "major": 0, "minor": 0}
    compliance_summary = {"pass": 0, "fail": 0, "warning": 0}
    
    for camera in cameras:
        period_stats = [
            stat for stat in camera.daily_stats
            if stat.date and start_date <= stat.date <= end_date
        ]
        
        if not period_stats:
            continue
        
        total_uptime = sum(s.total_uptime_seconds or 0 for s in period_stats)
        total_downtime = sum(s.total_downtime_seconds or 0 for s in period_stats)
        total_time = total_uptime + total_downtime
        uptime_percentage = (total_uptime / total_time * 100) if total_time > 0 else 100.0
        
        # Get incident count
        incident_count = db.query(func.count(CameraStatusChangeLog.id)).filter(
            CameraStatusChangeLog.camera_id == camera.id,
            CameraStatusChangeLog.previous_status == "Offline",
            CameraStatusChangeLog.changed_at >= start_date,
            CameraStatusChangeLog.changed_at <= end_datetime
        ).scalar() or 0
        
        # Calculate MTTR
        mttr, _ = _calculate_mttr_mtbf(db, camera.id, start_date, end_date)
        
        # Compliance status
        compliance_status = _get_sla_compliance_status(uptime_percentage, sla_threshold)
        compliance_summary[compliance_status.lower()] += 1
        
        report_data.append({
            "camera_name": camera.hostname,
            "location": camera.location or "-",
            "group": ", ".join(group.name for group in camera.groups) or "Ungrouped",
            "uptime_percentage": uptime_percentage,
            "total_uptime_hours": total_uptime / 3600,
            "total_downtime_hours": total_downtime / 3600,
            "incident_count": incident_count,
            "mttr_seconds": mttr,
            "compliance_status": compliance_status
        })
    
    # Sort by uptime (worst first)
    report_data.sort(key=lambda x: x["uptime_percentage"])
    
    # Calculate summary
    if report_data:
        avg_uptime = sum(d["uptime_percentage"] for d in report_data) / len(report_data)
        worst_performer = report_data[0]
        best_performer = max(report_data, key=lambda x: x["uptime_percentage"])
        total_cameras = len(report_data)
    else:
        avg_uptime = 100.0
        worst_performer = None
        best_performer = None
        total_cameras = 0
    
    # Generate report based on format
    if format == "pdf":
        return _generate_pdf_report(
            start_date, end_date, report_data, compliance_summary,
            avg_uptime, worst_performer, best_performer, total_cameras,
            sla_threshold, uptime_chart, heatmap_chart, include_charts,
            selected_groups=groups
        )
    elif format == "excel":
        return _generate_excel_report(
            start_date, end_date, report_data, compliance_summary,
            avg_uptime, worst_performer, best_performer, total_cameras,
            sla_threshold, selected_groups=groups
        )
    else:  # csv
        return _generate_csv_report(
            start_date, end_date, report_data, compliance_summary,
            avg_uptime, worst_performer, best_performer, total_cameras,
            sla_threshold, selected_groups=groups
        )


def _generate_pdf_report(
    start_date: date, end_date: date, report_data: list,
    compliance_summary: dict, avg_uptime: float,
    worst_performer: dict, best_performer: dict,
    total_cameras: int, sla_threshold: float,
    uptime_chart: str = None, heatmap_chart: str = None,
    include_charts: bool = True, selected_groups: List[str] = None
):
    """Generate professional PDF health report with enhanced features."""
    from reportlab.platypus import KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )
    
    doc.title = f"B-SNAP Health Report – {start_date} to {end_date}"
    doc.author = "B-SNAP Monitoring System"
    doc.subject = "Executive Health and SLA Compliance Report"
    
    styles = getSampleStyleSheet()
    
    # Custom styles for text wrapping
    wrapped_style = ParagraphStyle(
        'Wrapped',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        wordWrap='CJK',
    )
    
    elems = []
    
    # Calculate derived metrics
    total_incidents = sum(d["incident_count"] for d in report_data)
    total_downtime_hours = sum(d["total_downtime_hours"] for d in report_data)
    
    # Top performers (top 5 with Pass status)
    top_performers = sorted(
        [d for d in report_data if d["compliance_status"] == "Pass"],
        key=lambda x: x["uptime_percentage"],
        reverse=True
    )[:5]
    
    # Devices requiring attention - only Fail status, sorted by worst uptime
    worst_performers = sorted(
        [d for d in report_data if d["compliance_status"] == "Fail"],
        key=lambda x: x["uptime_percentage"]
    )[:5]
    
    # --- Cover Page ---
    logo_path = "static/icons/logo.png"
    try:
        elems.append(RLImage(logo_path, width=4 * cm, height=4 * cm))
    except Exception:
        pass
    
    elems.append(Spacer(1, 1 * cm))
    elems.append(Paragraph("B-SNAP Health Report", styles["Title"]))
    elems.append(Paragraph("Executive Summary & SLA Compliance", styles["Heading2"]))
    elems.append(Spacer(1, 0.5 * cm))
    elems.append(Paragraph(f"Period: {start_date.strftime('%d %B %Y')} – {end_date.strftime('%d %B %Y')}", styles["Normal"]))
    elems.append(Paragraph(f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')}", styles["Normal"]))
    if selected_groups:
        group_text = ", ".join(selected_groups) if len(selected_groups) <= 3 else f"{len(selected_groups)} groups selected"
        elems.append(Paragraph(f"Device Groups: {group_text}", styles["Normal"]))
    elems.append(PageBreak())
    
    # --- Executive Summary Section ---
    elems.append(Paragraph("Executive Summary", styles["Heading2"]))
    
    # Key metrics summary table
    summary_data = [
        ["Metric", "Value"],
        ["Total Devices Monitored", str(total_cameras)],
        ["Report Period", f"{start_date.strftime('%d %b %Y')} - {end_date.strftime('%d %b %Y')}"],
        ["Average Uptime", f"{avg_uptime:.2f}%"],
        ["Total Incidents", str(total_incidents)],
        ["Total Downtime", f"{total_downtime_hours:.2f} hours"],
        ["SLA Threshold", f"{sla_threshold}%"],
    ]
    
    summary_tbl = Table(summary_data, colWidths=[6 * cm, 6 * cm])
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F3F4F6")),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(summary_tbl)
    elems.append(Spacer(1, 0.5 * cm))
    
    summary_text = (
        f"This report provides a comprehensive overview of {total_cameras} cameras monitored by B-SNAP "
        f"during the period {start_date.strftime('%d %B %Y')} to {end_date.strftime('%d %B %Y')}. "
        f"The overall system availability is {avg_uptime:.2f}%, with {total_incidents} incidents recorded "
        f"resulting in {total_downtime_hours:.2f} hours of total downtime. "
    )
    if avg_uptime >= sla_threshold:
        summary_text += f"<b>The system meets the SLA target of {sla_threshold}%.</b>"
    else:
        summary_text += f"<b>Attention required: System is below the SLA target of {sla_threshold}%.</b>"
    
    elems.append(Paragraph(summary_text, styles["Normal"]))
    elems.append(Spacer(1, 0.5 * cm))
    
    # --- SLA Compliance Summary ---
    elems.append(Paragraph("SLA Compliance Summary", styles["Heading2"]))
    
    pass_count = compliance_summary.get("pass", 0)
    warning_count = compliance_summary.get("warning", 0)
    fail_count = compliance_summary.get("fail", 0)
    
    # Helper function to create visual bar (max 10 blocks)
    def make_bar(count, total):
        if total == 0:
            return ""
        bar_len = int(count / total * 10)
        return "■" * bar_len
    
    compliance_data = [
        ["Status", "Count", "Percentage", "Visual"],
        ["Pass", str(pass_count), 
         f"{(pass_count / total_cameras * 100):.1f}%" if total_cameras else "0%",
         make_bar(pass_count, total_cameras)],
        ["Warning", str(warning_count),
         f"{(warning_count / total_cameras * 100):.1f}%" if total_cameras else "0%",
         make_bar(warning_count, total_cameras)],
        ["Fail", str(fail_count),
         f"{(fail_count / total_cameras * 100):.1f}%" if total_cameras else "0%",
         make_bar(fail_count, total_cameras)],
    ]
    
    comp_tbl = Table(compliance_data, colWidths=[3 * cm, 2.5 * cm, 2.5 * cm, 3 * cm])
    comp_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (2, -1), "CENTER"),
        ("TEXTCOLOR", (0, 1), (0, 1), colors.HexColor("#059669")),
        ("TEXTCOLOR", (0, 2), (0, 2), colors.HexColor("#D97706")),
        ("TEXTCOLOR", (0, 3), (0, 3), colors.HexColor("#DC2626")),
        ("TEXTCOLOR", (3, 1), (3, 1), colors.HexColor("#059669")),
        ("TEXTCOLOR", (3, 2), (3, 2), colors.HexColor("#D97706")),
        ("TEXTCOLOR", (3, 3), (3, 3), colors.HexColor("#DC2626")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(comp_tbl)
    elems.append(Spacer(1, 0.5 * cm))
    
    # --- Key Highlights ---
    elems.append(Paragraph("Key Highlights", styles["Heading2"]))
    
    highlights = []
    if best_performer:
        highlights.append(f"<b>Best Overall Performance:</b> {best_performer['camera_name']} achieved {best_performer['uptime_percentage']:.2f}% uptime")
    if worst_performer:
        highlights.append(f"<b>Requires Attention:</b> {worst_performer['camera_name']} with {worst_performer['uptime_percentage']:.2f}% uptime ({worst_performer['incident_count']} incidents)")
    if total_incidents > 0:
        avg_incidents = total_incidents / total_cameras if total_cameras > 0 else 0
        highlights.append(f"<b>Incident Rate:</b> Average of {avg_incidents:.1f} incidents per device")
    
    for highlight in highlights:
        elems.append(Paragraph(f"• {highlight}", styles["Normal"]))
    elems.append(Spacer(1, 0.3 * cm))
    
    # --- Top Performers ---
    if top_performers:
        elems.append(Paragraph("Top Performers", styles["Heading3"]))
        top_data = [["Rank", "Device Name", "Uptime %", "Incidents", "Downtime (h)"]]
        for idx, performer in enumerate(top_performers, 1):
            top_data.append([
                str(idx),
                Paragraph(performer['camera_name'][:30], wrapped_style),
                f"{performer['uptime_percentage']:.2f}%",
                str(performer['incident_count']),
                f"{performer['total_downtime_hours']:.2f}"
            ])
        
        top_tbl = Table(top_data, colWidths=[1.5 * cm, 5 * cm, 2.5 * cm, 2 * cm, 2.5 * cm])
        top_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#059669")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 1), (0, -1), "CENTER"),
            ("ALIGN", (2, 1), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#ECFDF5")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elems.append(top_tbl)
        elems.append(Spacer(1, 0.3 * cm))
    
    # --- Devices Requiring Attention ---
    if worst_performers:
        elems.append(Paragraph("Devices Requiring Attention", styles["Heading3"]))
        worst_data = [["Rank", "Device Name", "Uptime %", "Incidents", "Downtime (h)"]]
        for idx, performer in enumerate(worst_performers, 1):
            worst_data.append([
                str(idx),
                Paragraph(performer['camera_name'][:30], wrapped_style),
                f"{performer['uptime_percentage']:.2f}%",
                str(performer['incident_count']),
                f"{performer['total_downtime_hours']:.2f}"
            ])
        
        worst_tbl = Table(worst_data, colWidths=[1.5 * cm, 5 * cm, 2.5 * cm, 2 * cm, 2.5 * cm])
        worst_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DC2626")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 1), (0, -1), "CENTER"),
            ("ALIGN", (2, 1), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FEF2F2")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        elems.append(worst_tbl)
        elems.append(Spacer(1, 0.5 * cm))
    else:
        # All devices meet SLA
        elems.append(Paragraph("Devices Requiring Attention", styles["Heading3"]))
        elems.append(Paragraph(
            "<font color='#059669'><b>✓ All devices meet SLA requirements.</b></font> "
            f"All {total_cameras} devices achieved uptime above {sla_threshold}% threshold.",
            styles["Normal"]
        ))
        elems.append(Spacer(1, 0.5 * cm))
    
    # --- Charts (if provided) ---
    if include_charts:
        def add_chart(b64_str, caption):
            if not b64_str:
                return
            try:
                import base64
                imgdata = base64.b64decode(b64_str.split(",")[1])
                reader = ImageReader(BytesIO(imgdata))
                elems.append(RLImage(reader, width=14 * cm, height=7 * cm))
                elems.append(Paragraph(caption, styles["Normal"]))
                elems.append(Spacer(1, 0.4 * cm))
            except Exception:
                logger.exception("Chart embedding failed")
        
        if uptime_chart or heatmap_chart:
            elems.append(PageBreak())
            elems.append(Paragraph("Visual Analytics", styles["Heading2"]))
            
        if uptime_chart:
            add_chart(uptime_chart, "Uptime Trend Over Time")
        if heatmap_chart:
            add_chart(heatmap_chart, "Outage Heatmap")
        if uptime_chart or heatmap_chart:
            elems.append(Spacer(1, 0.3 * cm))
    
    # --- Detailed Device Table ---
    elems.append(PageBreak())
    elems.append(Paragraph("Complete Device Details", styles["Heading2"]))
    
    # Create smaller wrapped style for device details table
    detail_wrapped_style = ParagraphStyle(
        'DetailWrapped',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        wordWrap='CJK',
    )
    
    # Table header with new columns
    table_data = [[
        "Device Name", "Location", "Group", "Uptime %", 
        "Downtime (h)", "Incidents", "MTTR (h)", "SLA Status"
    ]]
    
    # Table rows with text wrapping for device names
    for item in report_data:
        mttr_hours = (item["mttr_seconds"] or 0) / 3600
        
        # Truncate long text to prevent overflow
        device_name = item["camera_name"][:35] if len(item["camera_name"]) > 35 else item["camera_name"]
        location = item["location"][:25] if item["location"] and len(item["location"]) > 25 else (item["location"] or "-")
        group_name = item["group"][:15] if len(item["group"]) > 15 else item["group"]
        
        table_data.append([
            Paragraph(device_name, detail_wrapped_style),
            Paragraph(location, detail_wrapped_style),
            Paragraph(group_name, detail_wrapped_style),
            f"{item['uptime_percentage']:.2f}%",
            f"{item['total_downtime_hours']:.2f}",
            str(item["incident_count"]),
            f"{mttr_hours:.1f}",
            item["compliance_status"]
        ])
    
    # Create table with adjusted column widths - total width ~17cm for A4
    col_widths = [3.2 * cm, 2.5 * cm, 1.8 * cm, 1.7 * cm, 1.7 * cm, 1.4 * cm, 1.4 * cm, 1.8 * cm]
    device_tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    
    # Build style with conditional formatting for each row's SLA status
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 7),
        ("ALIGN", (3, 1), (6, -1), "CENTER"),
        ("ALIGN", (7, 1), (7, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Alternating row colors
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        # Allow table to split across pages
        ("SPLITBYROW", (0, 0), (-1, -1), True),
    ]
    
    # Add conditional formatting for each row based on compliance status
    for idx, item in enumerate(report_data, start=1):
        status_colors = {
            "Pass": colors.HexColor("#059669"),
            "Warning": colors.HexColor("#D97706"),
            "Fail": colors.HexColor("#DC2626")
        }
        status_color = status_colors.get(item["compliance_status"], colors.black)
        table_style.append(("TEXTCOLOR", (7, idx), (7, idx), status_color))
        table_style.append(("FONTNAME", (7, idx), (7, idx), "Helvetica-Bold"))
    
    device_tbl.setStyle(TableStyle(table_style))
    elems.append(device_tbl)
    
    # Footer
    elems.append(Spacer(1, 0.5 * cm))
    footer_text = f"Generated automatically by B-SNAP Monitoring System • {datetime.now().strftime('%d %B %Y %H:%M')}"
    elems.append(Paragraph(footer_text, styles["Normal"]))
    
    doc.build(elems)
    buf.seek(0)
    
    filename = f"b-snap-health-report_{start_date}_{end_date}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def _clean_ascii(text):
    """Helper to clean text for ASCII output."""
    if text is None:
        return ""
    if not isinstance(text, str):
        return str(text)
    return text.encode('ascii', 'replace').decode('ascii')


def _generate_excel_report(
    start_date: date, end_date: date, report_data: list,
    compliance_summary: dict, avg_uptime: float,
    worst_performer: dict, best_performer: dict,
    total_cameras: int, sla_threshold: float,
    selected_groups: List[str] = None
):
    """Generate Excel health report."""
    try:
        import pandas as pd
        
        # Create summary sheet data
        summary_data = {
            "Metric": [
                "Report Period Start",
                "Report Period End",
                "Total Cameras",
                "Average Uptime %",
                "SLA Threshold %",
                "Compliance Pass",
                "Compliance Warning",
                "Compliance Fail",
                "Best Performer",
                "Worst Performer"
            ],
            "Value": [
                start_date.isoformat(),
                end_date.isoformat(),
                total_cameras,
                round(avg_uptime, 2),
                sla_threshold,
                compliance_summary.get("pass", 0),
                compliance_summary.get("warning", 0),
                compliance_summary.get("fail", 0),
                f"{best_performer['camera_name']} ({best_performer['uptime_percentage']:.2f}%)" if best_performer else "N/A",
                f"{worst_performer['camera_name']} ({worst_performer['uptime_percentage']:.2f}%)" if worst_performer else "N/A",
            ]
        }
        
        # Prepare device details data for Excel (exclude internal fields)
        device_data = []
        for item in report_data:
            device_data.append({
                "Camera Name": item["camera_name"],
                "Location": item["location"],
                "Group": item["group"],
                "Uptime %": round(item["uptime_percentage"], 2),
                "Total Uptime (h)": round(item["total_uptime_hours"], 2),
                "Total Downtime (h)": round(item["total_downtime_hours"], 2),
                "Incidents": item["incident_count"],
                "MTTR (h)": round((item.get("mttr_seconds") or 0) / 3600, 1),
                "SLA Status": item["compliance_status"]
            })
        
        # Create Excel with multiple sheets
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Summary sheet
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)
            
            # Device details sheet
            if device_data:
                pd.DataFrame(device_data).to_excel(writer, sheet_name="Device Details", index=False)
        
        output.seek(0)
        filename = f"b-snap-health-report_{start_date}_{end_date}.xlsx"
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except ImportError:
        # Fallback to CSV
        return _generate_csv_report(
            start_date, end_date, report_data, compliance_summary,
            avg_uptime, worst_performer, best_performer, total_cameras, sla_threshold,
            selected_groups
        )
    except Exception as e:
        logger.error(f"Excel generation failed: {e}")
        # Fallback to CSV on any error
        return _generate_csv_report(
            start_date, end_date, report_data, compliance_summary,
            avg_uptime, worst_performer, best_performer, total_cameras, sla_threshold,
            selected_groups
        )


def _generate_csv_report(
    start_date: date, end_date: date, report_data: list,
    compliance_summary: dict, avg_uptime: float,
    worst_performer: dict, best_performer: dict,
    total_cameras: int, sla_threshold: float,
    selected_groups: List[str] = None
):
    """Generate CSV health report."""
    import csv
    from io import StringIO
    
    try:
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header info
        writer.writerow(["B-SNAP Health Report"])
        writer.writerow(["Period:", f"{start_date} to {end_date}"])
        writer.writerow(["Total Cameras:", total_cameras])
        writer.writerow(["Average Uptime %:", round(avg_uptime, 2)])
        writer.writerow(["SLA Threshold %:", sla_threshold])
        writer.writerow([])
        
        # Write compliance summary
        writer.writerow(["SLA Compliance Summary"])
        writer.writerow(["Status", "Count"])
        writer.writerow(["Pass", compliance_summary.get("pass", 0)])
        writer.writerow(["Warning", compliance_summary.get("warning", 0)])
        writer.writerow(["Fail", compliance_summary.get("fail", 0)])
        writer.writerow([])
        
        # Write device details
        if report_data:
            writer.writerow([
                "Camera Name", "Location", "Group", "Uptime %",
                "Total Uptime (h)", "Total Downtime (h)", "Incidents", "MTTR (h)", "SLA Status"
            ])
            for item in report_data:
                writer.writerow([
                    item["camera_name"],
                    item["location"],
                    item["group"],
                    round(item["uptime_percentage"], 2),
                    round(item["total_uptime_hours"], 2),
                    round(item["total_downtime_hours"], 2),
                    item["incident_count"],
                    round((item.get("mttr_seconds") or 0) / 3600, 1),
                    item["compliance_status"]
                ])
        
        csv_content = output.getvalue()
        output.close()
        
        filename = f"b-snap-health-report_{start_date}_{end_date}.csv"
        return StreamingResponse(
            BytesIO(csv_content.encode('utf-8')),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error(f"CSV generation failed: {e}")
        raise


# =============================================================================
# Scheduled Reports API
# =============================================================================

@router.get("/health/reports/scheduled")
async def list_scheduled_reports(
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """List all scheduled reports."""
    reports = db.query(ScheduledReport).order_by(ScheduledReport.created_at.desc()).all()
    return {
        "reports": [
            {
                "id": r.id,
                "name": r.name,
                "report_type": r.report_type,
                "format": r.format,
                "frequency": r.frequency,
                "is_active": r.is_active,
                "last_sent_at": r.last_sent_at.isoformat() if r.last_sent_at else None,
                "next_scheduled_at": r.next_scheduled_at.isoformat() if r.next_scheduled_at else None,
            }
            for r in reports
        ]
    }


@router.post("/health/reports/scheduled")
async def create_scheduled_report(
    name: str = Form(...),
    report_type: str = Form("health"),
    format: str = Form("pdf"),
    frequency: str = Form("weekly"),
    lookback_days: int = Form(7),
    sla_threshold: float = Form(99.5),
    recipients: str = Form(...),  # Comma-separated emails
    hour: int = Form(8),
    is_active: bool = Form(True),
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Create a new scheduled report."""
    report = ScheduledReport(
        name=name,
        report_type=report_type,
        format=format,
        frequency=frequency,
        lookback_days=lookback_days,
        sla_threshold=sla_threshold,
        recipients=recipients,
        hour=hour,
        is_active=is_active,
        created_by=current_operator.id
    )
    
    db.add(report)
    db.commit()
    db.refresh(report)
    
    return {"status": "success", "report_id": report.id}


@router.delete("/health/reports/scheduled/{report_id}")
async def delete_scheduled_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_operator: User = Depends(operator_access_required)
):
    """Delete a scheduled report."""
    report = db.query(ScheduledReport).filter(ScheduledReport.id == report_id).first()
    if not report:
        return JSONResponse(status_code=404, content={"message": "Report not found"})
    
    db.delete(report)
    db.commit()
    
    return {"status": "success", "message": "Report deleted"}
