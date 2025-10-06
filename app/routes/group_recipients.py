import logging
from fastapi import APIRouter, Depends, Request, Form, Path
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from app.db.database import get_db
from app.models.recipient import GroupRecipient
from app.models.camera_group import CameraGroup
from app.models.camera import Camera
from app.utils.audit_logger import log_audit
from app.utils.template_helper import templates
from app.utils.email_helper import send_email
from app.utils.response_helper import json_error_response, json_success_response
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("main")
router = APIRouter()


# ============================================================
# 1️⃣ RENDER HALAMAN UTAMA (HTML)
# ============================================================
@router.get("/recipients", name="list_recipients_page")
def list_recipients_page(request: Request, db: Session = Depends(get_db)):
    """Tampilkan halaman HTML (UI) untuk manajemen recipients."""
    groups = db.query(CameraGroup).all()
    return templates.TemplateResponse("recipients.html", {"request": request, "groups": groups})


# ============================================================
# 2️⃣ ENDPOINT JSON (untuk AJAX fetch di frontend)
# ============================================================
@router.get("/api/recipients", name="list_recipients_api")
def list_recipients_api(db: Session = Depends(get_db), page: int = 1, per_page: int = 10):
    """Kembalikan data recipients, groups, dan lokasi dalam format JSON."""
    try:
        groups = db.query(CameraGroup).all()
        query = db.query(GroupRecipient).options(joinedload(GroupRecipient.group))

        total = query.count()
        recipients = query.offset((page - 1) * per_page).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        # Ambil daftar lokasi per group
        locations_by_group = {}
        for g in groups:
            locs = (
                db.query(Camera.location)
                .filter(Camera.group_id == g.id)
                .distinct()
                .all()
            )
            locations_by_group[g.id] = [l[0] for l in locs if l[0]]

        return json_success_response(
            "Recipient data fetched successfully",
            {
                "recipients": [
                    {
                        "id": r.id,
                        "email": r.email,
                        "nickname": r.nickname,
                        "group_id": r.group_id,
                        "group_name": r.group.name if r.group else None,
                        "locations": r.locations,
                    }
                    for r in recipients
                ],
                "groups": [{"id": g.id, "name": g.name} for g in groups],
                "locations_by_group": locations_by_group,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total,
                    "total_pages": total_pages,
                },
            },
        )

    except Exception as e:
        logger.exception("Error fetching recipients JSON")
        return json_error_response(str(e), 500)


# ============================================================
# 3️⃣ ADD RECIPIENT
# ============================================================
@router.post("/recipients/add", name="add_recipient")
async def add_recipient(
    request: Request,
    email: str = Form(...),
    nickname: Optional[str] = Form(None),
    group_id: int = Form(...),
    db: Session = Depends(get_db),
):
    form = await request.form()
    locs = form.getlist("locations")
    locations_str = ",".join(locs) if locs else None

    try:
        recipient = GroupRecipient(
            email=email.strip(),
            nickname=nickname,
            group_id=group_id,
            locations=locations_str,
        )
        db.add(recipient)
        db.flush()
        db.commit()
        db.refresh(recipient)
        return json_success_response("Recipient added successfully", {"id": recipient.id})
    except IntegrityError:
        db.rollback()
        logger.exception("IntegrityError on add_recipient")
        return json_error_response(f"Email '{email}' sudah ada di group ini.", 400)
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error on add_recipient")
        return json_error_response(str(e), 500)


# ============================================================
# 4️⃣ EDIT RECIPIENT
# ============================================================
@router.post("/recipients/edit/{recipient_id}")
async def edit_recipient_submit(
    request: Request,
    recipient_id: int = Path(...),
    db: Session = Depends(get_db),
    email: str = Form(...),
    nickname: str = Form(None),
    group_id: int = Form(...),
):
    rec = db.query(GroupRecipient).filter(GroupRecipient.id == recipient_id).first()
    if not rec:
        return json_error_response("Recipient not found", 404)

    form = await request.form()
    locs = form.getlist("locations")
    locations_str = ",".join(locs) if locs else None
    email_norm = email.strip()

    before = {
        "email": rec.email,
        "nickname": rec.nickname,
        "group_id": rec.group_id,
        "locations": rec.locations,
    }

    try:
        rec.email = email_norm
        rec.nickname = nickname
        rec.group_id = group_id
        rec.locations = locations_str
        db.flush()
        db.commit()
        db.refresh(rec)
    except IntegrityError:
        db.rollback()
        logger.exception("IntegrityError on edit_recipient")
        return json_error_response(f"Email '{email_norm}' sudah ada di group ini.", 400)
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error on edit_recipient")
        return json_error_response(str(e), 500)

    after = {
        "email": rec.email,
        "nickname": rec.nickname,
        "group_id": rec.group_id,
        "locations": rec.locations,
    }
    changes = [f"- {k}: '{before[k]}' -> '{after[k]}'" for k in before if before[k] != after[k]]

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="update_recipient",
        target=rec.email,
        ip=request.client.host,
        extra="\n".join(changes) if changes else "No changes detected.",
    )

    return json_success_response("Recipient updated successfully")


# ============================================================
# 5️⃣ DELETE RECIPIENT
# ============================================================
@router.post("/recipients/delete/{recipient_id}")
async def delete_recipient_submit(
    request: Request,
    recipient_id: int = Path(...),
    db: Session = Depends(get_db),
):
    try:
        rec = db.query(GroupRecipient).filter(GroupRecipient.id == recipient_id).first()
        if not rec:
            return json_error_response("Recipient not found", 404)

        target_email = rec.email
        db.delete(rec)
        db.commit()

        log_audit(
            db=db,
            user=request.session.get("user_name", "unknown"),
            action="delete_recipient",
            target=target_email,
            ip=request.client.host,
            extra="Recipient deleted",
        )

        return json_success_response(f"Recipient {target_email} deleted")
    except Exception as e:
        db.rollback()
        logger.exception("Unexpected error on delete_recipient")
        return json_error_response(str(e), 500)
