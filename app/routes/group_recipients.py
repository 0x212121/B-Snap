import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from app.db.database import get_db
from app.models.recipient import GroupRecipient
from app.models.camera_group import CameraGroup
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

# === DELETE RECIPIENT ===
@router.post("/recipients/delete/{id}", name="delete_recipient")
def delete_recipient(id: int, db: Session = Depends(get_db)):
    recipient = db.query(GroupRecipient).filter(GroupRecipient.id == id).first()
    if recipient:
        db.delete(recipient)
        db.commit()
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


@router.post("/recipients/edit", name="edit_recipient")
def edit_recipient(
    id: int = Form(...),
    email: str = Form(...),
    nickname: str = Form(None),
    group_id: int = Form(...),
    db: Session = Depends(get_db),
):
    recipient = db.query(GroupRecipient).get(id)
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    recipient.email = email
    recipient.nickname = nickname
    recipient.group_id = group_id
    db.commit()
    return RedirectResponse(url="/recipients", status_code=303)

