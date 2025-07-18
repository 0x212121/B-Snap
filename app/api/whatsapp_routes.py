from fastapi import APIRouter, Depends
from app.db.database import get_db
from app.models_sql import WhatsappWhitelist
from sqlalchemy.orm import Session

router = APIRouter()


@router.get("/api/whatsapp/is-allowed")
def is_whatsapp_allowed(phone: str, db: Session = Depends(get_db)):
    entry = db.query(WhatsappWhitelist).filter_by(phone_number=phone, is_active=True).first()
    if entry:
        return {
            "allowed": True,
            "role": entry.role,
            "name": entry.name,
        }
    return {
        "allowed": False
    }