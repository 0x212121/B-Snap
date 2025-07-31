from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.models_sql import Camera as DBCamera, CameraGroup, User
from fastapi.responses import JSONResponse
from app.routes.auth import admin_access_required

router = APIRouter()

@router.get("/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(
    keyword: str = Query(..., description="Part of group name or camera name to search"),
    current_admin: User = Depends(admin_access_required),
):
    db: Session = SessionLocal()
    try:
        # --- Langkah 1: Coba filter berdasarkan group name dulu ---
        group_match = (
            db.query(DBCamera)
            .join(DBCamera.group)
            .options(joinedload(DBCamera.group))
            .filter(
                DBCamera.ip.isnot(None),
                DBCamera.ip != "",
                CameraGroup.name.ilike(f"%{keyword}%")
            )
            .all()
        )

        cameras = group_match

        # --- Langkah 2: Kalau tidak ada hasil, fallback ke pencarian berdasarkan hostname ---
        if not cameras:
            cameras = (
                db.query(DBCamera)
                .outerjoin(DBCamera.group)
                .options(joinedload(DBCamera.group))
                .filter(
                    DBCamera.ip.isnot(None),
                    DBCamera.ip != "",
                    DBCamera.hostname.ilike(f"%{keyword}%")
                )
                .all()
            )

        # Format hasil
        results = []
        for cam in cameras:
            results.append({
                "name": cam.hostname,
                "ip": cam.ip,
                "group": cam.group.name if cam.group else None
            })

        return {"count": len(results), "results": results}
    finally:
        db.close()