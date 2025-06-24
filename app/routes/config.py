from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.models_sql import Configuration
from app.db.database import SessionLocal
from app.scheduler import update_scheduler_config
from app.utils.decorators import admin_required

router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/config", response_class=HTMLResponse)
@admin_required
async def config_page(request: Request):
    db = SessionLocal()
    configs = db.query(Configuration).all()
    db.close()

    config_dict = {c.key: c.value for c in configs}
    return templates.TemplateResponse("config.html", {
        "request": request,
        "configs": config_dict
    })


@router.post("/config/save")
@admin_required
async def config_save(
    request: Request,
    snapshot_interval_minutes: int = Form(...),
    healthcheck_interval_minutes: int = Form(...),
    snapshot_concurrent_workers: int = Form(...),
    pagination_per_page: int = Form(...),
    max_screenshot_per_camera: int = Form(...),
    watermark_text: str = Form(...),
    map_title: str = Form(...)
):
    db = SessionLocal()
    keys = {
        "snapshot_interval_minutes": snapshot_interval_minutes,
        "healthcheck_interval_minutes": healthcheck_interval_minutes,
        "snapshot_concurrent_workers": snapshot_concurrent_workers,
        "pagination_per_page": pagination_per_page,
        "max_screenshot_per_camera": max_screenshot_per_camera,
        "watermark_text": watermark_text,
        "map_title": map_title
    }

    for key, value in keys.items():
        config = db.query(Configuration).filter_by(key=key).first()
        if config:
            config.value = str(value)
        else:
            config = Configuration(key=key, value=str(value))
            db.add(config)
    db.commit()
    db.close()

    return RedirectResponse(url="/config", status_code=303)


@router.post("/reload-config")
async def reload_config():
    update_scheduler_config()
    return {"message": "Config reloaded successfully"}