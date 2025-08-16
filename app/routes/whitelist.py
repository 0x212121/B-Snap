import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from app.db.database import get_db
from app.models.user import User
from app.models.whitelist import WhatsappWhitelist
from app.models.whitelist import RoleEnum
from sqlalchemy.orm import Session
from app.routes.auth import admin_access_required
from app.schemas.whitelist_schema import WhitelistOut, WhitelistCreate, WhitelistUpdate
from app.utils.audit_logger import log_audit
from app.core.logging_config import setup_logging
from datetime import datetime, timezone


router = APIRouter(tags=["Whitelist"])

setup_logging()
logger = logging.getLogger("management")


@router.get("/api/whitelist", response_model=list[WhitelistOut])
def get_whitelist(db: Session = Depends(get_db)):
    entries = db.query(WhatsappWhitelist).all()

    # pastikan added_at tidak None
    for entry in entries:
        if entry.added_at is None:
            entry.added_at = datetime.now(timezone.utc)

    return entries


@router.post("/api/whitelist")
def add_whitelist(
    request: Request,
    entry: WhitelistCreate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        logger.debug("Add whitelist request by user=%s ip=%s payload=%s",
                     current_admin.username, request.client.host, entry.model_dump())

        existing = db.query(WhatsappWhitelist).filter_by(phone_number=entry.phone_number).first()
        if existing:
            logger.debug("Phone number %s already exists, updating...", entry.phone_number)
            for field, value in entry.model_dump().items():
                setattr(existing, field, value)
            action = "update_whitelist"
        else:
            logger.debug("Phone number %s not found, creating new entry...", entry.phone_number)
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
        logger.info("Whitelist %s successful for phone=%s", action, entry.phone_number)
        return {"status": "success"}

    except Exception as e:
        logger.error("Error in add_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=f"Internal Server Error: {str(e)}")


@router.delete("/api/whitelist/{phone_number}")
def delete_whitelist(
    request: Request,
    phone_number: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        logger.debug("Delete whitelist request phone=%s by user=%s", phone_number, current_admin.username)

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
            logger.warning("Delete whitelist failed: phone=%s not found", phone_number)
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
        logger.info("Deleted whitelist phone=%s", phone_number)
        return {"status": "deleted"}

    except Exception as e:
        logger.error("Error in delete_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=f"Internal Server Error: {str(e)}")


@router.put("/api/whitelist/{phone_number}")
def edit_whitelist(
    request: Request,
    phone_number: str,
    data: WhitelistUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    try:
        logger.debug("Edit whitelist request phone=%s by user=%s payload=%s",
                     phone_number, current_admin.username, data.model_dump())

        entry = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone_number).first()

        if not entry:
            log_audit(
                db=db,
                user=current_admin.username,
                action="edit_whitelist_failed",
                target=phone_number,
                ip=request.client.host,
                extra={"error": "not found", **data.model_dump()}
            )
            logger.warning("Edit whitelist failed: phone=%s not found", phone_number)
            raise HTTPException(404, "Not found")

        if data.name is not None:
            entry.name = data.name
        if data.role is not None:
            entry.role = RoleEnum(data.role)
        if data.is_active is not None:
            entry.is_active = data.is_active
        
        # Update the group relationship
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
        logger.info("Updated whitelist phone=%s", phone_number)
        return {"status": "updated"}

    except Exception as e:
        logger.error("Error in edit_whitelist: %s", e, exc_info=True)
        db.rollback()
        raise HTTPException(500, detail=f"Internal Server Error: {str(e)}")
    
    
