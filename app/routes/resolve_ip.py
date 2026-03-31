from fastapi import APIRouter, Depends, Query
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
    current_admin: User = Depends(admin_access_required),
):
    keyword_clean = keyword.strip()
    if not keyword_clean:
        raise HTTPException(status_code=400, detail="Keyword cannot be empty or only whitespace")

    db: Session = SessionLocal()
    try:
        has_all = False
        whitelisted_group_ids: list[int] = []

        if phone_number:
            # --- Ambil whitelist berdasarkan nomor WA ---
            whitelist_entries = (
                db.query(WhatsappWhitelist)
                .filter(
                    WhatsappWhitelist.phone_number == phone_number,
                    WhatsappWhitelist.is_active == True
                )
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
            .outerjoin(Camera.group)
            .options(joinedload(Camera.group))
            .filter(
                Camera.ip.isnot(None),
                Camera.ip != "",
            )
        )

        # Step 1: cari berdasarkan group name
        group_match = base_query.filter(Camera.group.has(CameraGroup.name.ilike(f"%{keyword_clean}%")))
        if not has_all:
            group_match = group_match.filter(Camera.group_id.in_(whitelisted_group_ids))

        cameras = group_match.all()

        # Step 2: fallback hostname
        if not cameras:
            host_match = base_query.filter(Camera.hostname.ilike(f"%{keyword_clean}%"))
            if not has_all:
                host_match = host_match.filter(Camera.group_id.in_(whitelisted_group_ids))
            cameras = host_match.all()

        results = [
            {
                "id": cam.id,
                "name": cam.hostname,
                "ip": cam.ip,
                "group_id": cam.group_id,
                "group": cam.group.name if cam.group else None,
            }
            for cam in cameras
        ]

        return {"count": len(results), "results": results}
    finally:
        db.close()
