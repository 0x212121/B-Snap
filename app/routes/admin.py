from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.changelog_parser import parse_changelog_md
from app.utils.template_helper import templates

router = APIRouter(tags=["Admin"])


@router.get("/admin/whitelist", response_class=HTMLResponse)
async def whitelist_admin_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    return templates.TemplateResponse("whitelist.html", {
        "request": request,
        "title": "WhatsApp Whitelist",
        "admin": current_admin,
    })


@router.get("/api/changelog")
async def changelog_api():
    data = parse_changelog_md()
    return JSONResponse(content=data)


@router.get("/changelog", response_class=HTMLResponse)
async def changelog_html(request: Request):
    changelog_data = parse_changelog_md()
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "changelog": changelog_data
    })