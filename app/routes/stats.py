from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models_sql import CameraDailyStats, Camera as DBCamera, CameraHealth, SnapshotLog, User
from app.routes.auth import admin_access_required

router = APIRouter()
from app.utils.template_helper import templates


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

    all_cameras = [c[0].strip() for c in db.query(DBCamera.hostname).all()]
    cameras_with_data = {s.camera_name.strip() for s in stats if s.camera_name}
    cameras_without_data = [c for c in all_cameras if c not in cameras_with_data]

    context = {
        "request": request,
        "stats_grouped": dict(stats_grouped),
        "camera_names": sorted(cameras_with_data),
        "selected_camera": camera,
        "chart_labels": [d["date"] for d in chart_data],
        "chart_data": [d["snapshot_count"] for d in chart_data],
        "no_data_cameras": cameras_without_data,
        "total_cameras_without_data": len(cameras_without_data),
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

    # Ambil semua hostname kamera
    all_cameras = (
        db.query(DBCamera.hostname, CameraHealth.status)
        .join(CameraHealth, DBCamera.id == CameraHealth.id)
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