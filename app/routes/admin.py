from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.changelog_parser import (
    parse_changelog_md, 
    search_changelog, 
    paginate_changelog,
    get_changelog_stats
)
from app.utils.template_helper import templates

router = APIRouter(tags=["Admin"])


@router.get("/admin/wa-bot", response_class=HTMLResponse)
async def wa_bot_builder_page(
    request: Request,
    current_admin: User = Depends(admin_access_required),
):
    return templates.TemplateResponse("wa_bot_builder.html", {
        "request": request,
        "title": "WhatsApp Bot Builder",
        "admin": current_admin,
    })


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
async def changelog_api(
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=20),
    search: Optional[str] = Query(None),
):
    """Get changelog with pagination and search."""
    changelog = parse_changelog_md()
    
    # Apply search filter
    if search:
        changelog = search_changelog(changelog, search)
    
    # Get stats before pagination
    stats = get_changelog_stats(changelog)
    
    # Paginate
    paginated, current_page, total_pages = paginate_changelog(changelog, page, per_page)
    
    return JSONResponse({
        "versions": paginated,
        "stats": stats,
        "pagination": {
            "page": current_page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_items": len(changelog),
        }
    })


@router.get("/changelog", response_class=HTMLResponse)
async def changelog_html(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(5, ge=1, le=20),
    search: Optional[str] = Query(None),
):
    """Render changelog page with pagination support."""
    return templates.TemplateResponse("changelog.html", {
        "request": request,
        "page": page,
        "per_page": per_page,
        "search": search or "",
    })
