from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from fastapi.responses import JSONResponse
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required

router = APIRouter()

from fastapi import HTTPException

@router.get("/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(
    keyword: str = Query(..., description="Part of group name or camera name to search"),
    phone_number: str | None = Query(None, description="Whitelist phone number to filter group access"),
    sort_by: str = Query("hostname", description="Result field to sort by: hostname, ip, or id"),
    sort_order: str = Query("asc", description="Sort direction: asc or desc"),
    current_admin: User = Depends(admin_access_required),
):
    keyword_clean = keyword.strip()
    if not keyword_clean:
        raise HTTPException(status_code=400, detail="Keyword cannot be empty or only whitespace")

    sort_columns = {
        "hostname": func.lower(Camera.hostname),
        "name": func.lower(Camera.hostname),
        "ip": Camera.ip,
        "id": Camera.id,
    }
    sort_key = sort_by.strip().lower()
    direction = sort_order.strip().lower()
    if sort_key not in sort_columns:
        raise HTTPException(status_code=400, detail="sort_by must be one of: hostname, name, ip, id")
    if direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_order must be asc or desc")

    db: Session = SessionLocal()
    try:
        has_all = False
        whitelisted_group_ids: list[int] = []

        if phone_number:
            # --- Ambil whitelist berdasarkan nomor WA ---
            whitelist_entries = (
                db.query(WhatsappWhitelist)
                .filter(WhatsappWhitelist.phone_number == phone_number)
                .all()
            )

            if not whitelist_entries:
                return {"count": 0, "results": []}

            # cek apakah punya akses ke semua kamera
            # akses penuh jika: role admin, atau group_id is None (tidak terbatas group)
            has_all = any(
                entry.role == RoleEnum.admin or entry.group_id is None
                for entry in whitelist_entries
            )
            whitelisted_group_ids = [entry.group_id for entry in whitelist_entries if entry.group_id]
        else:
            # tanpa phone_number → akses penuh
            has_all = True

        # --- Base query kamera ---
        base_query = (
            db.query(Camera)
            .options(joinedload(Camera.groups))
            .filter(
                Camera.ip.isnot(None),
                Camera.ip != "",
            )
        )

        # Step 1: cari berdasarkan group name
        group_match = base_query.filter(Camera.groups.any(CameraGroup.name.ilike(f"%{keyword_clean}%")))
        if not has_all:
            group_match = group_match.filter(Camera.groups.any(CameraGroup.id.in_(whitelisted_group_ids)))

        sort_column = sort_columns[sort_key]
        order_column = sort_column.asc() if direction == "asc" else sort_column.desc()
        cameras = group_match.order_by(order_column, Camera.id.asc()).all()

        # Step 2: fallback hostname
        if not cameras:
            host_match = base_query.filter(Camera.hostname.ilike(f"%{keyword_clean}%"))
            if not has_all:
                host_match = host_match.filter(Camera.groups.any(CameraGroup.id.in_(whitelisted_group_ids)))
            cameras = host_match.order_by(order_column, Camera.id.asc()).all()

        results = [
            {
                "id": cam.id,
                "name": cam.hostname,
                "ip": cam.ip,
                "group_id": cam.group_id,
                "group": cam.group.name if cam.group else None,
                "camera_groups": [{"id": group.id, "name": group.name} for group in cam.groups],
            }
            for cam in cameras
        ]

        return {"count": len(results), "results": results}
    finally:
        db.close()
