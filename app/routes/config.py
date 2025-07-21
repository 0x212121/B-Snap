import os
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.models_sql import Configuration
from app.db.database import SessionLocal
from app.jobs.scheduler import update_scheduler_config
from app.utils.decorators import admin_required
from fastapi import UploadFile, File, Form, Request
from fastapi.responses import RedirectResponse
from PIL import Image
import os
import shutil

router = APIRouter()

from app.utils.template_helper import templates


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


MAX_LOGO_SIZE = 512 * 1024  # 512 KB
ALLOWED_EXTENSIONS = {".png", ".ico"}
ALLOWED_MIME_TYPES = {"image/png", "image/x-icon"}

@router.post("/config/save")
@admin_required
async def config_save(
    request: Request,
    snapshot_interval_minutes: int = Form(...),
    healthcheck_interval_minutes: int = Form(...),
    snapshot_concurrent_workers: int = Form(...),
    items_per_page: int = Form(...),
    max_screenshot_per_camera: int = Form(...),
    watermark_text: str = Form(...),
    map_title: str = Form(...),
    app_logo: UploadFile = File(None)
):
    db = SessionLocal()
    keys = {
        "snapshot_interval_minutes": snapshot_interval_minutes,
        "healthcheck_interval_minutes": healthcheck_interval_minutes,
        "snapshot_concurrent_workers": snapshot_concurrent_workers,
        "items_per_page": items_per_page,
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

    # Hardened logo upload
    if app_logo and app_logo.filename:
        ext = os.path.splitext(app_logo.filename)[-1].lower()
        content_type = app_logo.content_type

        # Validate extension & MIME type
        if ext not in ALLOWED_EXTENSIONS or content_type not in ALLOWED_MIME_TYPES:
            db.close()
            return RedirectResponse(url="/config?error=InvalidLogoFormat", status_code=303)

        # Read file content
        contents = await app_logo.read()
        if len(contents) > MAX_LOGO_SIZE:
            db.close()
            return RedirectResponse(url="/config?error=FileTooLarge", status_code=303)

        # Validate image integrity with Pillow
        try:
            from io import BytesIO
            image = Image.open(BytesIO(contents))
            image.verify()  # This checks for integrity
        except Exception:
            db.close()
            return RedirectResponse(url="/config?error=CorruptImage", status_code=303)

        # Save file securely
        save_path = os.path.join("static", "icons", f"logo{ext}")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as buffer:
            buffer.write(contents)

    db.commit()
    db.close()
    return RedirectResponse(url="/config", status_code=303)


@router.post("/reload-config")
async def reload_config():
    with open("/tmp/reload_scheduler.flag", "w") as f:
        f.write("reload")
    return {"message": "Reload flag created"}