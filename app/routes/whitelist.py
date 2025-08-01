from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from app.db.database import get_db
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist
from sqlalchemy.orm import Session
from app.routes.auth import admin_access_required
from app.schemas.whitelist_schema import WhitelistOut, WhitelistCreate
from app.utils.audit_logger import log_audit

router = APIRouter(tags=["Whitelist"])


@router.get("/api/whitelist", response_model=List[WhitelistOut])
def list_whitelist(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    result = db.query(WhatsappWhitelist).all()

    return result

# Add or update entry
@router.post("/api/whitelist")
def add_whitelist(request: Request, entry: WhitelistCreate, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    existing = db.query(WhatsappWhitelist).filter_by(phone_number=entry.phone_number).first()
    if existing:
        for field, value in entry.model_dump().items():
            setattr(existing, field, value)
        action = "update_whitelist"
    else:
        new_entry = WhatsappWhitelist(**entry.model_dump())
        db.add(new_entry)
        action = "create_whitelist"

    db.commit()
    log_audit(
        db=db,
        user=current_admin.username,
        action=action,
        target=entry.phone_number,
        ip=request.client.host,
        extra=entry.model_dump()
    )
    return {"status": "success"}



# Delete
@router.delete("/api/whitelist/{phone_number}")
def delete_whitelist(request: Request, phone_number: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone_number).first()
    if not entry:
        log_audit(
            db=db,
            user=current_admin.username,
            action="delete_whitelist_failed",
            target=phone_number,
            ip=request.client.host,
            extra={"error": "not found"}
        )
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

# Edit entry
@router.put("/api/whitelist/{phone_number}")
def edit_whitelist(request: Request, phone_number: str, update: WhitelistCreate, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone_number).first()
    if not entry:
        log_audit(
            db=db,
            user=current_admin.username,
            action="edit_whitelist_failed",
            target=phone_number,
            ip=request.client.host,
            extra={"error": "not found", **update.model_dump()}
        )
        raise HTTPException(404, "Not found")
    for field, value in update.model_dump().items():
        setattr(entry, field, value)
    db.commit()
    log_audit(
        db=db,
        user=current_admin.username,
        action="edit_whitelist",
        target=phone_number,
        ip=request.client.host,
        extra=update.model_dump()
    )
    return {"status": "updated"}
