from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm import Query as SQLQuery

from app.db.database import get_db
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist
from app.routes.auth import admin_access_required
from app.utils.wa_gateway import format_phone_number

router = APIRouter()


@router.get("/api/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(
    keyword: str = Query(..., description="Part of group name or camera name to search"),
    phone_number: str | None = Query(None, description="Required whitelisted sender phone number; its camera group restrictions apply"),
    sort_by: str = Query("hostname", description="Result field to sort by: hostname, ip, or id"),
    sort_order: str = Query("asc", description="Sort direction: asc or desc"),
    current_admin: User = Depends(admin_access_required),
    db: Session = Depends(get_db),
) -> dict:
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

    phone = format_phone_number(re.split(r"[@:]", str(phone_number or "").strip(), maxsplit=1)[0])
    if not phone:
        raise HTTPException(status_code=403, detail="phone_number is required and must be on the WhatsApp whitelist.")
    entry = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone).first()
    if not entry:
        raise HTTPException(status_code=403, detail="Phone number is not on the WhatsApp whitelist.")
    # A whitelist admin still follows its assigned group, just like native commands.
    group_ids = {group_id for group_id in (entry.group_id, current_admin.group_id) if group_id is not None}

    # --- Base query kamera ---
    base_query = (
        db.query(Camera)
        .options(joinedload(Camera.groups))
        .filter(
            Camera.ip.isnot(None),
            Camera.ip != "",
        )
    )

    sort_column = sort_columns[sort_key]
    order_column = sort_column.asc() if direction == "asc" else sort_column.desc()

    def restrict(query: SQLQuery) -> SQLQuery:
        for group_id in group_ids:
            query = query.filter((Camera.group_id == group_id) | Camera.groups.any(CameraGroup.id == group_id))
        return query

    # Prefer matching groups, then hostnames, while filtering every returned row.
    group_match = base_query.filter(Camera.groups.any(CameraGroup.name.ilike(f"%{keyword_clean}%")))
    host_match = base_query.filter(Camera.hostname.ilike(f"%{keyword_clean}%"))
    cameras = restrict(group_match).order_by(order_column, Camera.id.asc()).all()
    if not cameras:
        cameras = restrict(host_match).order_by(order_column, Camera.id.asc()).all()
    if not cameras:
        if group_match.first() or host_match.first():
            raise HTTPException(status_code=403, detail="You do not have permission to access the matching cameras or their groups.")
        raise HTTPException(status_code=404, detail="No camera with an IP address matches the provided keyword.")

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
