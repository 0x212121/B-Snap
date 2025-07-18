from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models_sql import User
from app.utils.auth_token import admin_access_required
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/admin/whitelist", response_class=HTMLResponse)
async def whitelist_admin_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    return templates.TemplateResponse("admin/whitelist.html", {
        "request": request,
        "title": "WhatsApp Whitelist",
        "admin": current_admin,
    })
