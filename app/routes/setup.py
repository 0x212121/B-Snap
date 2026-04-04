from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from passlib.hash import bcrypt
from app.models.user import User
from app.db.database import SessionLocal

router = APIRouter()

# Setup templates
from app.utils.template_helper import templates

@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    db = SessionLocal()
    user_exists = db.query(User).first()
    db.close()
    if user_exists:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request})

@router.post("/setup")
def setup_create(username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        if db.query(User).first():
            return RedirectResponse(url="/login", status_code=302)

        hashed = bcrypt.hash(password)
        # NOTE: group_id is None - admin has access to all cameras by default
        # No need for special "ALL" group anymore
        user = User(username=username, password=hashed, role="admin", group_id=None)

        db.add(user)
        db.commit()
        db.refresh(user)

        return RedirectResponse(url="/login", status_code=302)

    finally:
        db.close()


