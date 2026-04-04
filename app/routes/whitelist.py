import logging
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, Request, Query, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone

from app.db.database import get_db
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist, RoleEnum
from app.models.camera_group import CameraGroup
from app.routes.auth import admin_access_required
from app.schemas.whitelist_schema import WhitelistCreate, WhitelistUpdate
from app.utils.audit_logger import log_audit
from app.core.logging_config import setup_logging


router = APIRouter(tags=["Whitelist"])
setup_logging()
logger = logging.getLogger("management")


# ============================================================
# GET WHITELIST with pagination + search + group info + stats
# ============================================================
@router.get("/api/whitelist")
def get_whitelist(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1),
    search: str | None = Query(None),
    db: Session = Depends(get_db)
):
    if limit not in [10, 20, 50, 100]:
        limit = 20

    query = db.query(WhatsappWhitelist).options(joinedload(WhatsappWhitelist.group))

    if search:
        query = query.filter(
            or_(
                WhatsappWhitelist.phone_number.ilike(f"%{search}%"),
                WhatsappWhitelist.name.ilike(f"%{search}%")
            )
        )

    total = query.count()
    entries = (
        query.order_by(WhatsappWhitelist.added_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    results = []
    for e in entries:
        group_name = e.group.name if e.group else None
        results.append({
            "phone_number": e.phone_number,
            "name": e.name,
            "role": e.role.value if e.role else None,
            "group_id": e.group_id,
            "group_name": group_name,
            "added_at": e.added_at or datetime.now(timezone.utc),
        })

    # Calculate stats
    total_entries = db.query(WhatsappWhitelist).count()
    
    # Role distribution
    admin_count = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.role == RoleEnum.admin).count()
    user_count = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.role == RoleEnum.user).count()

    return {
        "data": results,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
        "stats": {
            "total": total_entries,
            "admins": admin_count,
            "users": user_count,
        }
    }


# ============================================================
# GET GROUPS BY PHONE
# ============================================================
@router.get("/api/whitelist/group/{phone_number}", response_class=JSONResponse)
def get_whitelist_group(phone_number: str, db: Session = Depends(get_db)):
    entries = (
        db.query(WhatsappWhitelist)
        .filter(WhatsappWhitelist.phone_number == phone_number)
        .options(joinedload(WhatsappWhitelist.group))
        .all()
    )
    data = [{"group_name": e.group.name if e.group else None, "group_id": e.group_id} for e in entries]
    return JSONResponse(content={"results": data})


# ============================================================
# ADD / UPSERT WHITELIST
# ============================================================
@router.post("/api/whitelist")
def add_whitelist(
    request: Request,
    entry: WhitelistCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        logger.debug("Add whitelist request by %s (%s)", current_admin.username, request.client.host)

        # 🚫 Cek apakah nomor sudah ada
        existing = db.query(WhatsappWhitelist).filter_by(phone_number=entry.phone_number).first()
        if existing:
            logger.warning("Attempt to add duplicate phone_number: %s", entry.phone_number)
            raise HTTPException(
                status_code=409,
                detail=f"Phone number {entry.phone_number} already exists in whitelist."
            )

        # ✅ Jika belum ada, lanjutkan insert
        new_entry = WhatsappWhitelist(**entry.model_dump())
        db.add(new_entry)
        db.commit()

        log_audit(
            db=db,
            user=current_admin.username,
            action="create_whitelist",
            target=entry.phone_number,
            ip=request.client.host,
            extra=entry.model_dump()
        )

        logger.info("Added new whitelist phone=%s", entry.phone_number)
        return {"status": "success"}

    except HTTPException:
        raise  # biarkan FastAPI kirim langsung 409
    except Exception as e:
        logger.error("Error in add_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=f"Internal Server Error: {str(e)}")


# ============================================================
# DELETE
# ============================================================
@router.delete("/api/whitelist/{phone_number}")
def delete_whitelist(
    request: Request,
    phone_number: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone_number).first()
        if not entry:
            raise HTTPException(404, "Not found")

        db.delete(entry)
        db.commit()

        log_audit(
            db=db,
            user=current_admin.username,
            action="delete_whitelist",
            target=phone_number,
            ip=request.client.host
        )
        return {"status": "deleted"}
    except Exception as e:
        logger.error("Error in delete_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=str(e))


# ============================================================
# EDIT
# ============================================================
@router.put("/api/whitelist/{phone_number}")
def edit_whitelist(
    request: Request,
    phone_number: str,
    data: WhitelistUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone_number).first()
        if not entry:
            raise HTTPException(404, "Not found")

        if data.name is not None:
            entry.name = data.name
        if data.role is not None:
            entry.role = RoleEnum(data.role)

        if data.group_id is not None:
            entry.group_id = data.group_id

        db.commit()
        db.refresh(entry)

        log_audit(
            db=db,
            user=current_admin.username,
            action="edit_whitelist",
            target=phone_number,
            ip=request.client.host,
            extra=data.model_dump()
        )
        return {"status": "updated"}
    except Exception as e:
        logger.error("Error in edit_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=str(e))


# ============================================================
# EXPORT WHITELIST TO CSV
# ============================================================
@router.get("/api/whitelist/export")
def export_whitelist_csv(
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Export all whitelist entries to CSV."""
    try:
        entries = db.query(WhatsappWhitelist).options(joinedload(WhatsappWhitelist.group)).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["phone_number", "name", "role", "group_name"])
        
        for e in entries:
            writer.writerow([
                e.phone_number,
                e.name or "",
                e.role.value if e.role else "user",
                e.group.name if e.group else ""
            ])
        
        csv_content = output.getvalue()
        output.close()
        
        return StreamingResponse(
            io.BytesIO(csv_content.encode('utf-8')),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=whitelist_export.csv"}
        )
    except Exception as e:
        logger.error("Error exporting whitelist: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


# ============================================================
# BULK IMPORT WHITELIST FROM CSV
# ============================================================
@router.post("/api/whitelist/import")
async def import_whitelist_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Bulk import whitelist entries from CSV file.
    
    CSV Format: phone_number,name,role,group_name
    - phone_number: required, format: 628xxxxxxxxxx
    - name: optional
    - role: optional, default 'user' (values: user, admin)
    - group_name: optional
    """
    # Validation errors that should return JSON (not raise HTTPException to avoid HTML error pages)
    if not file.filename.endswith('.csv'):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "File must be a CSV"}
        )
    
    try:
        content = await file.read()
        content_str = content.decode('utf-8')
        
        csv_file = io.StringIO(content_str)
        reader = csv.DictReader(csv_file)
        
        if not reader.fieldnames or 'phone_number' not in reader.fieldnames:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "CSV must have a 'phone_number' column"}
            )
        
        # Cache groups for lookup
        groups = {g.name: g.id for g in db.query(CameraGroup).all()}
        
        imported = 0
        skipped = 0
        errors = []
        
        for row_num, row in enumerate(reader, start=2):
            try:
                phone = row.get('phone_number', '').strip()
                name = row.get('name', '').strip() or None
                role_str = row.get('role', 'user').strip().lower()
                group_name = row.get('group_name', '').strip()
                if not phone:
                    errors.append(f"Row {row_num}: Missing phone_number")
                    skipped += 1
                    continue
                
                # Validate phone format
                if not phone.startswith('628') or not phone[3:].isdigit():
                    errors.append(f"Row {row_num}: Invalid phone format '{phone}'. Must start with 628")
                    skipped += 1
                    continue
                
                # Check if already exists
                existing = db.query(WhatsappWhitelist).filter_by(phone_number=phone).first()
                if existing:
                    skipped += 1
                    continue
                
                # Parse role
                role = RoleEnum.admin if role_str == 'admin' else RoleEnum.user
                
                # Find group_id
                group_id = groups.get(group_name) if group_name else None
                if group_name and not group_id:
                    errors.append(f"Row {row_num}: Group '{group_name}' not found")
                    # Continue anyway, just without group
                
                # Create entry
                entry = WhatsappWhitelist(
                    phone_number=phone,
                    name=name,
                    role=role,
                    group_id=group_id
                )
                db.add(entry)
                imported += 1
                
            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                skipped += 1
        
        db.commit()
        
        # Log audit
        log_audit(
            db=db,
            user=current_admin.username,
            action="bulk_import_whitelist",
            target="whitelist",
            ip=request.client.host,
            extra=f"Imported: {imported}, Skipped: {skipped}, Errors: {len(errors)}"
        )
        
        return {
            "status": "success",
            "message": f"Import complete: {imported} imported, {skipped} skipped",
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:10]
        }
        
    except Exception as e:
        db.rollback()
        logger.error("Error importing whitelist: %s", e, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Internal Server Error: {str(e)}"}
        )
