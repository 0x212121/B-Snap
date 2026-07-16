from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.models.camera import Camera
from app.models.nvr import NVR
from app.models.record_check import RecordCheckRun, RecordFolderMapping, RecordFolderStatus, RecordSource
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.auth import verify_password
from app.utils.audit_logger import log_audit
from app.utils.record_check import run_record_source_check, validate_record_source_path
from app.utils.template_helper import templates

router = APIRouter(tags=["Record Checks"])


class RecordSourcePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    base_path: str = Field(..., min_length=1)
    enabled: bool = True
    nvr_id: str | None = None
    stale_threshold_seconds: int = Field(4200, ge=60, le=604800)
    long_dead_threshold_seconds: int = Field(604800, ge=3600, le=31536000)
    scan_depth: int = Field(1, ge=1, le=1)


class RecordFolderMappingPayload(BaseModel):
    camera_id: str | None = None


class RecordSourceDeletePayload(BaseModel):
    password: str = Field(..., min_length=1)
    confirmation_name: str = Field(..., min_length=1)


def _validate_source_payload(payload: RecordSourcePayload) -> None:
    validate_record_source_path(payload.base_path)
    if payload.stale_threshold_seconds >= payload.long_dead_threshold_seconds:
        raise ValueError("Stale threshold must be lower than long-dead threshold")


def _source_to_dict(source: RecordSource, latest_run: RecordCheckRun | None = None) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "base_path": source.base_path,
        "enabled": source.enabled,
        "nvr_id": source.nvr_id,
        "nvr_name": source.nvr.hostname if source.nvr else None,
        "stale_threshold_seconds": source.stale_threshold_seconds,
        "long_dead_threshold_seconds": source.long_dead_threshold_seconds,
        "scan_depth": source.scan_depth,
        "latest_run": {
            "id": latest_run.id,
            "started_at": latest_run.started_at.isoformat() if latest_run.started_at else None,
            "status": latest_run.status,
            "total_folders": latest_run.total_folders,
            "healthy_count": latest_run.healthy_count,
            "stale_count": latest_run.stale_count,
            "long_dead_count": latest_run.long_dead_count,
            "unknown_count": latest_run.unknown_count,
            "error_message": latest_run.error_message,
        }
        if latest_run
        else None,
    }


@router.get("/admin/record-checks", response_class=HTMLResponse)
async def record_checks_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    nvrs = db.query(NVR).order_by(NVR.hostname).all()
    return templates.TemplateResponse("record_checks.html", {"request": request, "nvrs": nvrs})


@router.get("/api/record-checks/sources", response_class=JSONResponse)
async def list_record_sources(
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    sources = (
        db.query(RecordSource)
        .options(joinedload(RecordSource.nvr))
        .order_by(RecordSource.name)
        .all()
    )
    latest_runs = {}
    for run in (
        db.query(RecordCheckRun)
        .order_by(RecordCheckRun.source_id, RecordCheckRun.started_at.desc())
        .all()
    ):
        latest_runs.setdefault(run.source_id, run)
    return {"sources": [_source_to_dict(source, latest_runs.get(source.id)) for source in sources]}


@router.post("/api/record-checks/sources", response_class=JSONResponse)
async def create_record_source(
    request: Request,
    payload: RecordSourcePayload,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    try:
        _validate_source_payload(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    existing = db.query(RecordSource).filter(func.lower(RecordSource.name) == payload.name.strip().lower()).first()
    if existing:
        return JSONResponse(status_code=409, content={"status": "error", "message": "Record source name already exists"})

    if payload.nvr_id and not db.query(NVR).filter(NVR.id == payload.nvr_id).first():
        return JSONResponse(status_code=400, content={"status": "error", "message": "Selected NVR not found"})

    source = RecordSource(
        name=payload.name.strip(),
        base_path=str(validate_record_source_path(payload.base_path)),
        enabled=payload.enabled,
        nvr_id=payload.nvr_id,
        stale_threshold_seconds=payload.stale_threshold_seconds,
        long_dead_threshold_seconds=payload.long_dead_threshold_seconds,
        scan_depth=1,
    )
    db.add(source)
    db.commit()

    log_audit(
        db=db,
        user=current_admin.username,
        action="create_record_source",
        target=source.name,
        ip=request.client.host if request.client else None,
        extra={"base_path": source.base_path, "enabled": source.enabled},
    )
    return {"status": "success", "message": "Record source created", "source": _source_to_dict(source)}


@router.put("/api/record-checks/sources/{source_id}", response_class=JSONResponse)
async def update_record_source(
    request: Request,
    payload: RecordSourcePayload,
    source_id: str = Path(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    source = db.query(RecordSource).filter(RecordSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Record source not found")

    try:
        _validate_source_payload(payload)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    duplicate = (
        db.query(RecordSource)
        .filter(func.lower(RecordSource.name) == payload.name.strip().lower(), RecordSource.id != source_id)
        .first()
    )
    if duplicate:
        return JSONResponse(status_code=409, content={"status": "error", "message": "Record source name already exists"})

    if payload.nvr_id and not db.query(NVR).filter(NVR.id == payload.nvr_id).first():
        return JSONResponse(status_code=400, content={"status": "error", "message": "Selected NVR not found"})

    before = {
        "name": source.name,
        "base_path": source.base_path,
        "enabled": source.enabled,
        "nvr_id": source.nvr_id,
        "stale_threshold_seconds": source.stale_threshold_seconds,
        "long_dead_threshold_seconds": source.long_dead_threshold_seconds,
    }
    source.name = payload.name.strip()
    source.base_path = str(validate_record_source_path(payload.base_path))
    source.enabled = payload.enabled
    source.nvr_id = payload.nvr_id
    source.stale_threshold_seconds = payload.stale_threshold_seconds
    source.long_dead_threshold_seconds = payload.long_dead_threshold_seconds
    source.scan_depth = 1
    db.commit()

    log_audit(
        db=db,
        user=current_admin.username,
        action="update_record_source",
        target=source.name,
        ip=request.client.host if request.client else None,
        extra={"before": before, "after": {"name": source.name, "base_path": source.base_path, "enabled": source.enabled}},
    )
    return {"status": "success", "message": "Record source updated", "source": _source_to_dict(source)}


@router.delete("/api/record-checks/sources/{source_id}", response_class=JSONResponse)
async def delete_record_source(
    request: Request,
    payload: RecordSourceDeletePayload,
    source_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    source = db.query(RecordSource).options(joinedload(RecordSource.nvr)).filter(RecordSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Record source not found")

    expected_confirmation = source.nvr.hostname if source.nvr else source.name
    if payload.confirmation_name.strip() != expected_confirmation:
        log_audit(
            db=db,
            user=current_admin.username,
            action="delete_record_source_failed",
            target=source.name,
            ip=request.client.host if request.client else None,
            extra={"reason": "confirmation_name_mismatch"},
        )
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Confirmation name does not match.",
            },
        )

    if not verify_password(payload.password, current_admin.password):
        log_audit(
            db=db,
            user=current_admin.username,
            action="delete_record_source_failed",
            target=source.name,
            ip=request.client.host if request.client else None,
            extra={"reason": "invalid_password"},
        )
        return JSONResponse(
            status_code=401,
            content={"status": "error", "message": "Invalid password."},
        )

    name = source.name
    nvr_name = source.nvr.hostname if source.nvr else None
    db.delete(source)
    db.commit()
    log_audit(
        db=db,
        user=current_admin.username,
        action="delete_record_source",
        target=name,
        ip=request.client.host if request.client else None,
        extra={"nvr_name": nvr_name, "confirmed_with": expected_confirmation},
    )
    return {"status": "success", "message": "Record source deleted"}


@router.post("/api/record-checks/sources/{source_id}/run", response_class=JSONResponse)
async def run_record_source_now(
    request: Request,
    source_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    source = db.query(RecordSource).filter(RecordSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Record source not found")

    try:
        result = run_record_source_check(db, source, send_notifications=True)
        log_audit(
            db=db,
            user=current_admin.username,
            action="run_record_source_check",
            target=source.name,
            ip=request.client.host if request.client else None,
            extra=result,
        )
        return {"status": "success", "message": "Record check completed", "result": result}
    except Exception as exc:
        log_audit(
            db=db,
            user=current_admin.username,
            action="run_record_source_check_failed",
            target=source.name,
            ip=request.client.host if request.client else None,
            extra=str(exc),
        )
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("/api/record-checks/status", response_class=JSONResponse)
async def get_record_status(
    source_id: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    query = db.query(RecordFolderStatus).options(
        joinedload(RecordFolderStatus.source),
        joinedload(RecordFolderStatus.camera),
    )
    if source_id:
        query = query.filter(RecordFolderStatus.source_id == source_id)
    if status:
        query = query.filter(RecordFolderStatus.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Camera, RecordFolderStatus.camera_id == Camera.id, isouter=True).filter(
            or_(RecordFolderStatus.folder_name.ilike(like), Camera.hostname.ilike(like))
        )

    rows = query.order_by(RecordFolderStatus.status.asc(), RecordFolderStatus.folder_name.asc()).limit(limit).all()
    summary_query = db.query(RecordFolderStatus.status, func.count(RecordFolderStatus.id))
    if source_id:
        summary_query = summary_query.filter(RecordFolderStatus.source_id == source_id)
    summary = {row[0]: row[1] for row in summary_query.group_by(RecordFolderStatus.status).all()}

    return {
        "summary": {
            "healthy": summary.get("healthy", 0),
            "stale": summary.get("stale", 0),
            "long_dead": summary.get("long_dead", 0),
            "unknown": summary.get("unknown", 0),
            "missing": summary.get("missing", 0),
        },
        "items": [
            {
                "id": row.id,
                "source": row.source.name if row.source else None,
                "source_id": row.source_id,
                "folder_name": row.folder_name,
                "folder_path": row.folder_path,
                "camera": row.camera.hostname if row.camera else None,
                "camera_id": row.camera_id,
                "last_mtime": row.last_mtime.isoformat() if row.last_mtime else None,
                "age_seconds": row.age_seconds,
                "status": row.status,
                "status_changed_at": row.status_changed_at.isoformat() if row.status_changed_at else None,
                "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
                "alert_active": row.alert_active,
            }
            for row in rows
        ],
    }


@router.get("/api/record-checks/cameras", response_class=JSONResponse)
async def list_record_mapping_cameras(
    q: str | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    query = db.query(Camera)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Camera.hostname.ilike(like), Camera.ip.ilike(like), Camera.location.ilike(like)))
    cameras = query.order_by(Camera.hostname.asc()).limit(limit).all()
    return {
        "cameras": [
            {
                "id": camera.id,
                "hostname": camera.hostname,
                "ip": camera.ip,
                "location": camera.location,
                "status": camera.status,
            }
            for camera in cameras
        ]
    }


@router.put("/api/record-checks/status/{status_id}/mapping", response_class=JSONResponse)
async def update_record_folder_mapping(
    request: Request,
    payload: RecordFolderMappingPayload,
    status_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    status_row = db.query(RecordFolderStatus).filter(RecordFolderStatus.id == status_id).first()
    if not status_row:
        raise HTTPException(status_code=404, detail="Record folder status not found")

    camera = None
    if payload.camera_id:
        camera = db.query(Camera).filter(Camera.id == payload.camera_id).first()
        if not camera:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Camera not found"})

    mapping = (
        db.query(RecordFolderMapping)
        .filter(
            RecordFolderMapping.source_id == status_row.source_id,
            func.lower(RecordFolderMapping.folder_name) == status_row.folder_name.lower(),
        )
        .first()
    )
    if not mapping:
        mapping = RecordFolderMapping(source_id=status_row.source_id, folder_name=status_row.folder_name)
        db.add(mapping)

    mapping.camera_id = payload.camera_id
    status_row.camera_id = payload.camera_id
    db.commit()

    log_audit(
        db=db,
        user=current_admin.username,
        action="update_record_folder_mapping",
        target=status_row.folder_name,
        ip=request.client.host if request.client else None,
        extra={
            "source_id": status_row.source_id,
            "camera_id": payload.camera_id,
            "camera": camera.hostname if camera else None,
        },
    )
    return {
        "status": "success",
        "message": "Folder mapping updated",
        "camera": camera.hostname if camera else None,
        "camera_id": payload.camera_id,
    }


@router.get("/api/record-checks/runs", response_class=JSONResponse)
async def get_record_runs(
    source_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    query = db.query(RecordCheckRun).options(joinedload(RecordCheckRun.source))
    if source_id:
        query = query.filter(RecordCheckRun.source_id == source_id)
    runs = query.order_by(RecordCheckRun.started_at.desc()).limit(limit).all()
    return {
        "runs": [
            {
                "id": run.id,
                "source": run.source.name if run.source else None,
                "source_id": run.source_id,
                "started_at": run.started_at.isoformat() if isinstance(run.started_at, datetime) else None,
                "ended_at": run.ended_at.isoformat() if run.ended_at else None,
                "status": run.status,
                "total_folders": run.total_folders,
                "healthy_count": run.healthy_count,
                "stale_count": run.stale_count,
                "long_dead_count": run.long_dead_count,
                "unknown_count": run.unknown_count,
                "error_message": run.error_message,
            }
            for run in runs
        ]
    }
