from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

from app.utils.template_helper import templates


@router.get("/docs", response_class=HTMLResponse)
async def get_docs():
    # return HTMLResponse(content=open("docs.html").read())
    return RedirectResponse(url="/", status_code=302)