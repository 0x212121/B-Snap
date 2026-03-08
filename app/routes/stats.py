from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import not_, and_
from app.db.database import get_db
from app.models.camera_daily_stats import CameraDailyStats
from app.models.camera import Camera as DBCamera
from app.models.health import CameraHealth
from app.models.snapshot_log import SnapshotLog
from app.models.user import User
from app.models.task_timing import TaskTiming
from app.routes.auth import admin_access_required
from app.utils.timezone_helper import to_current_timezone, format_datetime_with_tz
from app.utils.template_helper import templates
import logging

router = APIRouter(tags=["Observability"])
logger = logging.getLogger("main")
storage_logger = logging.getLogger("storage")


@router.get("/stats", response_class=HTMLResponse)
async def get_camera_stats(
    request: Request,
    camera: str = Query(None),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required) 
):
    query = db.query(CameraDailyStats).filter(CameraDailyStats.snapshot_count > 0)  # ✅ hanya yang punya snapshot

    if camera:
        query = query.filter(CameraDailyStats.camera_name == camera)

    stats = query.order_by(CameraDailyStats.date).all()
    stats_grouped = defaultdict(list)
    chart_data = []
    snapshot_count_by_date = defaultdict(int)

    for stat in stats:
        date_str = stat.date.strftime('%Y-%m-%d')
        stat.date_str = date_str
        stats_grouped[stat.camera_name].append(stat)

        if camera:
            chart_data.append({
                "date": date_str,
                "snapshot_count": stat.snapshot_count
            })
        else:
            snapshot_count_by_date[date_str] += stat.snapshot_count

    if not camera:
        chart_data = [
            {"date": d, "snapshot_count": snapshot_count_by_date[d]}
            for d in sorted(snapshot_count_by_date)
        ]

    all_cameras = (
        db.query(DBCamera.hostname)
        .join(CameraHealth, DBCamera.id == CameraHealth.id)
        .filter(and_(
            not_(CameraHealth.status.ilike("standalone")),
            DBCamera.ip.isnot(None),
            DBCamera.ip != ""
        ))
        .all()
    )
    all_cameras = [c[0].strip() for c in all_cameras]
    cameras_with_data = {s.camera_name.strip() for s in stats if s.camera_name}
    cameras_without_data = [c for c in all_cameras if c not in cameras_with_data]

    timing_logs = (
        db.query(TaskTiming)
        .filter(TaskTiming.task_name == "scheduled_snapshot")
        .order_by(TaskTiming.started_at.desc())
        .limit(10)
        .all()
    )

    for log in timing_logs:
        log.started_at_local = format_datetime_with_tz(to_current_timezone(log.started_at, db))
        log.ended_at_local = format_datetime_with_tz(to_current_timezone(log.ended_at, db))

        # Format duration jadi hh:mm:ss
        seconds = int((log.duration_ms or 0) / 1000)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        log.duration_formatted = f"{hours:02}:{minutes:02}:{secs:02}"

    context = {
        "request": request,
        "stats_grouped": dict(stats_grouped),
        "camera_names": sorted(cameras_with_data),
        "selected_camera": camera,
        "chart_labels": [d["date"] for d in chart_data],
        "chart_data": [d["snapshot_count"] for d in chart_data],
        "no_data_cameras": cameras_without_data,
        "total_cameras_without_data": len(cameras_without_data),
        "timing_logs": timing_logs,
    }

    return templates.TemplateResponse("stats.html", context)


@router.get("/stats/data")
async def get_camera_stats_data(
    request: Request,
    camera: str = Query(None),
    days: int = Query(7),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    # Gunakan timezone-aware date
    now = datetime.now(timezone.utc).astimezone()
    end_date = now.date()
    start_date = end_date - timedelta(days=days)

    query = db.query(CameraDailyStats).filter(CameraDailyStats.date >= start_date)

    if camera:
        # ilike = case-insensitive (kompatibel PostgreSQL)
        query = query.filter(CameraDailyStats.camera_name.ilike(camera))

    stats = query.order_by(CameraDailyStats.date).all()

    stats_grouped = defaultdict(list)
    snapshot_count_by_date = {
        (start_date + timedelta(days=i)).strftime('%Y-%m-%d'): 0
        for i in range((end_date - start_date).days + 1)
    }

    for stat in stats:
        date_str = stat.date.strftime('%Y-%m-%d')
        stats_grouped[stat.camera_name].append(stat)
        snapshot_count_by_date[date_str] += stat.snapshot_count

    chart_data = [
        {"date": d, "snapshot_count": snapshot_count_by_date[d]}
        for d in sorted(snapshot_count_by_date)
    ]

    # Kamera yang punya data
    cameras_with_data = {s.camera_name for s in stats}

    cameras_without_data = []
    if not camera:
        all_cameras = (
            db.query(DBCamera.hostname, CameraHealth.status)
            .join(CameraHealth, DBCamera.id == CameraHealth.id)
            .filter(and_(
                not_(CameraHealth.status.ilike("standalone")),
                DBCamera.ip.isnot(None),
                DBCamera.ip != ""
            ))
            .all()
        )

        cameras_without_data = [
            {"hostname": hostname, "status": status}
            for hostname, status in all_cameras
            if hostname not in cameras_with_data
        ]

    return JSONResponse({
        "chart_labels": [d["date"] for d in chart_data],
        "chart_data": [d["snapshot_count"] for d in chart_data],
        "no_data_cameras": cameras_without_data,
        "total_cameras_without_data": len(cameras_without_data)
    })


@router.get("/stats/no-data-cameras", response_class=JSONResponse)
async def get_no_data_cameras(
    request: Request,
    days: int = Query(7, ge=1),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    # Timezone-aware date
    now = datetime.now(timezone.utc).astimezone()
    end_date = now.date()
    start_date = end_date - timedelta(days=days)

    # Ambil semua hostname kamera (exclude standalone & cameras without IP)
    all_cameras = (
        db.query(DBCamera.hostname, CameraHealth.status)
        .join(CameraHealth, DBCamera.id == CameraHealth.id)
        .filter(and_(
            not_(CameraHealth.status.ilike("standalone")),
            DBCamera.ip.isnot(None),
            DBCamera.ip != ""
        ))
        .all()
    )

    print(f"📅 Checking cameras with no data from {start_date} to {end_date}")
    print(f"🎥 Total cameras: {len(all_cameras)}")

    # Ambil camera_name dari snapshot
    snapshot_camera_names = (
        db.query(CameraDailyStats.camera_name)
        .filter(CameraDailyStats.date >= start_date,
                CameraDailyStats.snapshot_count > 0)
        .distinct()
        .all()
    )
    snapshot_camera_names = {c[0].strip() for c in snapshot_camera_names if c[0]}

    print(f"🎞️ Snapshot camera names: {len(snapshot_camera_names)}")

    # Bandingkan berdasarkan nama kamera
    no_data_cameras = [
        {"hostname": hostname, "status": status}
        for hostname, status in all_cameras
        if hostname.strip() not in snapshot_camera_names
    ]

    print(f"❌ Cameras with no data: {len(no_data_cameras)}")

    return {
        "days_range": days,
        "total": len(no_data_cameras),
        "cameras": no_data_cameras
    }


@router.get("/stats/heatmap", response_class=JSONResponse)
async def get_snapshot_heatmap(
    request: Request,
    camera: str = Query(None),
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    sg_tz = timezone("Asia/Singapore")

    # Set end and start datetime in Singapore timezone
    end_datetime = datetime.now(sg_tz)
    start_datetime = end_datetime - timedelta(days=days)

    query = db.query(SnapshotLog).filter(SnapshotLog.timestamp >= start_datetime)
    if camera:
        query = query.filter(SnapshotLog.camera_name == camera)

    logs = query.all()

    # Init heatmap data structure
    heatmap_data = defaultdict(lambda: [0] * 24)
    date_set = set()

    for log in logs:
        # Ensure log.timestamp is timezone-aware first
        if log.timestamp.tzinfo is None:
            # Assume it's in UTC if not aware
            log_ts = log.timestamp.replace(tzinfo=timezone("UTC"))
        else:
            log_ts = log.timestamp

        # Convert to Singapore time
        timestamp = log_ts.astimezone(sg_tz)
        date_str = timestamp.strftime("%Y-%m-%d")
        hour = timestamp.hour
        heatmap_data[date_str][hour] += 1
        date_set.add(date_str)

    sorted_dates = sorted(list(date_set))

    datasets = [
        {
            "label": date,
            "data": heatmap_data[date]
        }
        for date in sorted_dates
    ]

    return {
        "labels": [f"{h:02d}:00" for h in range(24)],
        "datasets": datasets
    }


# ----------------------------
# Storage Monitoring Endpoints
# ----------------------------
@router.get("/storage/status", response_class=JSONResponse)
async def get_storage_status(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Get current storage status and metrics.
    If no metrics exist, record one now.
    """
    from app.utils.storage_monitor import get_latest_storage_status, format_bytes, record_storage_metric
    
    metric = get_latest_storage_status(db)
    
    if not metric:
        # No metrics yet, record one now
        try:
            metric = record_storage_metric(db)
        except Exception as e:
            storage_logger.warning("Could not record storage metric: %s", e)
    
    if not metric:
        # Still no metric (error occurred), return empty status
        return {
            "status": "unknown",
            "usage_percent": 0,
            "total": "0 B",
            "used": "0 B",
            "free": "0 B",
            "breakdown": {},
            "growth_rate": 0,
            "days_until_full": -1,
            "alert_level": "unknown",
            "last_updated": None
        }
    
    return {
        "status": metric.alert_level,
        "usage_percent": round(metric.usage_percent, 1),
        "total": format_bytes(metric.total_bytes),
        "used": format_bytes(metric.used_bytes),
        "free": format_bytes(metric.free_bytes),
        "breakdown": {
            "snapshots": format_bytes(metric.snapshots_bytes),
            "videos": format_bytes(metric.videos_bytes),
            "logs": format_bytes(metric.logs_bytes),
            "other": format_bytes(metric.other_bytes),
            "snapshots_percent": round(metric.snapshots_bytes / metric.used_bytes * 100, 1) if metric.used_bytes > 0 else 0,
            "videos_percent": round(metric.videos_bytes / metric.used_bytes * 100, 1) if metric.used_bytes > 0 else 0,
            "logs_percent": round(metric.logs_bytes / metric.used_bytes * 100, 1) if metric.used_bytes > 0 else 0,
        },
        "growth_rate": round(metric.daily_growth_rate, 2),
        "days_until_full": round(metric.days_until_full, 1) if metric.days_until_full < 9999 else -1,
        "alert_level": metric.alert_level,
        "alert_message": metric.alert_message,
        "last_updated": metric.timestamp.isoformat() if metric.timestamp else None
    }


@router.get("/storage/trend", response_class=JSONResponse)
async def get_storage_trend(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Get storage usage trend for the last N days.
    """
    from app.utils.storage_monitor import get_storage_trend
    from datetime import datetime
    
    metrics = get_storage_trend(db, days)
    
    data = []
    for m in metrics:
        data.append({
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "usage_percent": round(m.usage_percent, 1),
            "used_gb": round(m.used_bytes / (1024**3), 2),
            "free_gb": round(m.free_bytes / (1024**3), 2),
            "snapshots_gb": round(m.snapshots_bytes / (1024**3), 2),
            "videos_gb": round(m.videos_bytes / (1024**3), 2),
        })
    
    return {
        "days": days,
        "data_points": len(data),
        "trend": data
    }


@router.get("/storage/alerts", response_class=JSONResponse)
async def get_storage_alerts(
    request: Request,
    unresolved_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Get storage alert history.
    """
    from app.models.storage_metric import StorageAlert
    
    query = db.query(StorageAlert)
    
    if unresolved_only:
        query = query.filter(StorageAlert.resolved_at.is_(None))
    
    alerts = query.order_by(StorageAlert.timestamp.desc()).limit(limit).all()
    
    return {
        "count": len(alerts),
        "alerts": [
            {
                "id": a.id,
                "timestamp": a.timestamp.isoformat() if a.timestamp else None,
                "level": a.level,
                "message": a.message,
                "usage_percent": round(a.usage_percent, 1),
                "free_gb": round(a.free_gb, 2),
                "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
                "resolved_by": a.resolved_by
            }
            for a in alerts
        ]
    }


@router.post("/storage/check-now", response_class=JSONResponse)
async def trigger_storage_check(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Manually trigger storage check.
    """
    from app.utils.storage_monitor import record_storage_metric
    from app.utils.audit_logger import log_audit
    storage_logger.info("Manual storage check triggered by %s", current_admin.username)
    
    try:
        metric = record_storage_metric(db)
        storage_logger.info("Storage metric recorded: %.1f%% usage", metric.usage_percent)
        
        # Log audit (separate try-except to not fail if audit fails)
        try:
            log_audit(
                db=db,
                user=current_admin.username,
                action="storage_check_manual",
                target="storage_monitor",
                ip=request.client.host if request.client else None,
                extra={"usage_percent": metric.usage_percent, "alert_level": metric.alert_level}
            )
        except Exception as audit_error:
            logger.warning("Failed to log audit: %s", audit_error)
        
        return {
            "success": True,
            "message": "Storage check completed",
            "usage_percent": round(metric.usage_percent, 1),
            "alert_level": metric.alert_level
        }
    except Exception as e:
        logger.error("Storage check failed: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)}
        )