import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Path
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from app.db.database import get_db
from app.models.recipient import GroupRecipient
from app.models.camera_group import CameraGroup
from app.utils.audit_logger import log_audit
from app.utils.template_helper import templates
from app.utils.email_helper import send_email

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/recipients", name="list_recipients")
def list_recipients(request: Request, db: Session = Depends(get_db), page: int = 1, per_page: int = 10):
    logger.debug(">>> THIS IS DEBUG from %s", __name__)
    logger.info(">>> THIS IS INFO from %s", __name__)
    logger.warning(">>> THIS IS WARNING from %s", __name__)
    try:
        groups = db.query(CameraGroup).all()
        query = db.query(GroupRecipient).options(joinedload(GroupRecipient.group))

        total = query.count()
        recipients = query.offset((page-1)*per_page).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        logger.info("Recipients page=%s, total=%s, groups=%s", page, total, len(groups))

        # LAKUKAN DEBUGGING DATA MENTAH DI SINI 💡
        logger.debug(f"Recipients count: {len(recipients)}")
        for r in recipients:
             # Jika ini gagal dicetak, masalahnya ada di objek ORM itu sendiri
             logger.debug(f"Recipient ID: {r.id}, Group Name: {r.group.name if r.group else 'N/A'}") 
        return templates.TemplateResponse(
            "recipients.html",
            {"request": request, "recipients": recipients, "groups": groups, "page": page,
             "per_page": per_page, "total_pages": total_pages}
        )
    except Exception as e:
        import traceback
        traceback.print_exc() # 
        logger.exception("Error loading recipients page")
        raise


# === ADD RECIPIENT ===
@router.post("/recipients/add", name="add_recipient")
def add_recipient(
    email: str = Form(...),
    nickname: Optional[str] = Form(None),
    group_id: int = Form(...),
    db: Session = Depends(get_db),
):
    recipient = GroupRecipient(email=email, nickname=nickname, group_id=group_id)
    db.add(recipient)
    db.commit()
    db.refresh(recipient)

    return RedirectResponse(url="/recipients", status_code=303)


@router.post("/recipients/send/{group_id}")
def send_test_email(group_id: int, db: Session = Depends(get_db)):
    recipients = db.query(GroupRecipient).filter(GroupRecipient.group_id == group_id).all()
    emails = [r.email for r in recipients]

    if not emails:
        return {"error": "No recipients in this group"}

    subject = "🔔 Test Notification"
    body = "Ini contoh pesan notifikasi via B-SNAP."
    html = "<h3>📢 Notifikasi</h3><p>Ini contoh email dari B-SNAP.</p>"

    send_email(emails, subject, body, html)
    return {"status": "Email sent", "recipients": emails}


@router.post("/recipients/edit/{recipient_id}", status_code=200)
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
        raise HTTPException(status_code=404, detail="Recipient not found")

    before = {"email": rec.email, "nickname": rec.nickname, "group_id": rec.group_id}
    rec.email, rec.nickname, rec.group_id = email, nickname, group_id
    db.commit()
    db.refresh(rec)

    after = {"email": rec.email, "nickname": rec.nickname, "group_id": rec.group_id}
    changes = [f"- {k}: '{before[k]}' -> '{after[k]}'" for k in before if before[k] != after[k]]

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="update_recipient",
        target=rec.email,
        ip=request.client.host,
        extra="\n".join(changes) if changes else "No changes detected."
    )

    return {"status": "success", "message": "Recipient updated successfully"}


@router.post("/recipients/delete/{recipient_id}", status_code=200)
async def delete_recipient_submit(
    request: Request,
    recipient_id: int = Path(...),
    db: Session = Depends(get_db),
):
    rec = db.query(GroupRecipient).filter(GroupRecipient.id == recipient_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recipient not found")

    target_email = rec.email
    db.delete(rec)
    db.commit()

    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="delete_recipient",
        target=target_email,
        ip=request.client.host,
        extra="Recipient deleted"
    )

    return {"status": "success", "message": f"Recipient {target_email} deleted"}
