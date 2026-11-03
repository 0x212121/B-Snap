from fastapi import APIRouter, Depends
from app.db.database import get_db
from app.models.whitelist import WhatsappWhitelist
from sqlalchemy.orm import Session
from app.routes.auth import admin_access_required
from app.utils.phone_numbers import whatsapp_phone_variants

router = APIRouter(dependencies=[Depends(admin_access_required)])


@router.get("/api/whatsapp/is-allowed")
def is_whatsapp_allowed(phone: str, db: Session = Depends(get_db)):
    variants = whatsapp_phone_variants(phone)
    if not variants:
        return {"allowed": False}
    entry = db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number.in_(variants)).first()
    if entry:
        return {
            "allowed": True,
            "role": entry.role,
            "name": entry.name,
            "group": entry.group_name
        }
    return {"allowed": False}
