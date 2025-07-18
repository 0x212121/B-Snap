from typing import List
from fastapi import APIRouter, Depends, HTTPException
from app.db.database import get_db
from app.models_sql import User, WhatsappWhitelist
from sqlalchemy.orm import Session
from app.routes.auth import admin_access_required
from app.schemas.whitelist_schema import WhitelistOut, WhitelistCreate

router = APIRouter()


@router.get("/api/whitelist", response_model=List[WhitelistOut])
def list_whitelist(db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    return db.query(WhatsappWhitelist).all()

# Add or update entry
@router.post("/api/whitelist")
def add_whitelist(entry: WhitelistCreate, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    existing = db.query(WhatsappWhitelist).filter_by(phone_number=entry.phone_number).first()
    if existing:
        for field, value in entry.dict().items():
            setattr(existing, field, value)
    else:
        new_entry = WhatsappWhitelist(**entry.dict())
        db.add(new_entry)
    db.commit()
    return {"status": "success"}


# Delete
@router.delete("/api/whitelist/{phone_number}")
def delete_whitelist(phone_number: str, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone_number).first()
    if not entry:
        raise HTTPException(404, "Not found")
    db.delete(entry)
    db.commit()
    return {"status": "deleted"}
