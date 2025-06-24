from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.utils.decorators import admin_required

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/developer/docs", response_class=HTMLResponse)
@admin_required
async def embedded_docs(request: Request):
    return templates.TemplateResponse("swagger_embed.html", {"request": request})