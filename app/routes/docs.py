from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/docs", response_class=HTMLResponse)
async def get_docs():
    # return HTMLResponse(content=open("docs.html").read())
    return RedirectResponse(url="/", status_code=302)