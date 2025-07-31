from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from app.db.database import SessionLocal
from app.models_sql import Camera as DBCamera, CameraGroup, User
from fastapi.responses import JSONResponse
from app.routes.auth import admin_access_required

router = APIRouter()

from fastapi import HTTPException

@router.get("/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(
    keyword: str = Query(..., description="Part of group name or camera name to search"),
    current_admin: User = Depends(admin_access_required),
):
    keyword_clean = keyword.strip()

    # Tolak input kosong atau spasi saja
    if not keyword_clean:
        raise HTTPException(status_code=400, detail="Keyword cannot be empty or only whitespace")

    db: Session = SessionLocal()
    try:
        # Step 1: Filter by group name
        group_match = (
            db.query(DBCamera)
            .join(DBCamera.group)
            .options(joinedload(DBCamera.group))
            .filter(
                DBCamera.ip.isnot(None),
                DBCamera.ip != "",
                CameraGroup.name.ilike(f"%{keyword_clean}%")
            )
            .all()
        )

        cameras = group_match

        # Step 2: Fallback to camera hostname
        if not cameras:
            cameras = (
                db.query(DBCamera)
                .outerjoin(DBCamera.group)
                .options(joinedload(DBCamera.group))
                .filter(
                    DBCamera.ip.isnot(None),
                    DBCamera.ip != "",
                    DBCamera.hostname.ilike(f"%{keyword_clean}%")
                )
                .all()
            )

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
