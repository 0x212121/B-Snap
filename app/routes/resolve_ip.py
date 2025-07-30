from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models_sql import Camera as DBCamera, User
from fastapi.responses import JSONResponse

from app.routes.auth import admin_access_required

router = APIRouter()

@router.get("/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(
    keyword: str = Query(..., description="Part of camera name to search"),
    current_admin: User = Depends(admin_access_required),
):
    db: Session = SessionLocal()
    try:
        # Ambil semua kamera dari database yang memiliki IP
        all_cameras = db.query(DBCamera).filter(DBCamera.ip.isnot(None)).all()

        results = []

        for cam in all_cameras:
            name_lower = cam.hostname.lower()
            keyword_lower = keyword.lower()

            # Cocokkan jika nama kamera mengandung keyword dan tidak diawali dengan "m7 "
            if keyword_lower in name_lower:
                results.append({
                    "name": cam.hostname,
                    "ip": cam.ip
                })

        return {"count": len(results), "results": results}
    finally:
        db.close()
