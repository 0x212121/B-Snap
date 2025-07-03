import os
import shutil
from fastapi import APIRouter, File, Form, Request, UploadFile
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
    map_title: str = Form(...),
    app_logo: UploadFile = File(None)  # Optional logo file
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

    # Save the application logo if provided
    if app_logo and app_logo.filename:
        ext = os.path.splitext(app_logo.filename)[-1].lower()
        if ext not in [".png", ".ico"]:
            db.close()
            return RedirectResponse(url="/config?error=InvalidLogoFormat", status_code=303)

        save_path = os.path.join("static", "icons", f"logo{ext}")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(app_logo.file, buffer)

    db.commit()
    db.close()

    return RedirectResponse(url="/config", status_code=303)


@router.post("/reload-config")
async def reload_config():
    update_scheduler_config()
    return {"message": "Config reloaded successfully"}