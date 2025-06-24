from fastapi import APIRouter, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models_sql import Camera as DBCamera
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/cctv/resolve-ip", response_class=JSONResponse)
def resolve_ip_by_name(keyword: str = Query(..., description="Part of camera name to search")):
    db: Session = SessionLocal()
    try:
        # Ambil semua kamera dari database
        all_cameras = db.query(DBCamera).all()

        # Simpan hasil
        results = []

        for cam in all_cameras:
            name_lower = cam.hostname.lower()
            keyword_lower = keyword.lower()

            # Hanya cocokkan jika nama kamera dimulai atau diakhiri dengan keyword
            if keyword_lower in name_lower and not name_lower.startswith("m7 "):
                results.append({
                    "name": cam.hostname,
                    "ip": cam.ip
                })

        return {"count": len(results), "results": results}
    finally:
        db.close()
