import os
from typing import Optional
from fastapi import APIRouter, File, Form, Request, Depends, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
import pytz
from PIL import Image
from sqlalchemy.orm import Session
from app.models.config import Configuration
from app.models.user import User
from app.db.database import get_db
from app.routes.auth import admin_access_required
from app.utils.template_helper import templates
from app.core.logging_config import set_debug_mode
from io import BytesIO

router = APIRouter(tags=["Config"])


@router.get("/config", response_class=HTMLResponse)
async def config_page(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    configs = db.query(Configuration).all()
    # db.close() tidak diperlukan karena Depends(get_db) akan menutupnya secara otomatis
    config_dict = {c.key: c.value for c in configs}
    timezones = pytz.common_timezones
    return templates.TemplateResponse("config.html", {
        "request": request,
        "configs": config_dict,
        "timezones": timezones
    })


MAX_LOGO_SIZE = 512 * 1024  # 512 KB
ALLOWED_EXTENSIONS = {".png", ".ico"}
ALLOWED_MIME_TYPES = {"image/png", "image/x-icon"}


@router.post("/config/save")
async def config_save(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
    snapshot_interval_minutes: int = Form(...),
    healthcheck_interval_minutes: int = Form(...),
    snapshot_concurrent_workers: int = Form(...),
    max_screenshot_per_camera: int = Form(...),
    snapshot_batch_size: int = Form(...),
    snapshot_batch_delay_seconds: int = Form(...),
    watermark_text: str = Form(...),
    map_title: str = Form(...),
    timezone: str = Form(...),
    debug_mode: Optional[bool] = Form(False),
    app_logo: UploadFile = File(None)
):
    # Validasi timezone
    if timezone not in pytz.all_timezones:
        # Mengembalikan JSONResponse dengan status 400 Bad Request
        return JSONResponse(status_code=400, content={"message": "Invalid timezone selected."})
    
    # Validasi batch size
    if snapshot_batch_size > 150 or snapshot_batch_size < 1:
        # Mengembalikan JSONResponse dengan status 400 Bad Request
        return JSONResponse(status_code=400, content={"message": "Snapshot batch size must be between 1 and 150."})

    keys = {
        "snapshot_interval_minutes": snapshot_interval_minutes,
        "healthcheck_interval_minutes": healthcheck_interval_minutes,
        "snapshot_concurrent_workers": snapshot_concurrent_workers,
        "max_screenshot_per_camera": max_screenshot_per_camera,
        "snapshot_batch_size": snapshot_batch_size,
        "snapshot_batch_delay_seconds": snapshot_batch_delay_seconds,
        "watermark_text": watermark_text,
        "map_title": map_title,
        "timezone": timezone,
        "debug_mode": str(int(debug_mode))
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
            return JSONResponse(status_code=400, content={"message": "Invalid logo file format. Only .png or .ico are allowed."})

        # Read file content
        contents = await app_logo.read()
        if len(contents) > MAX_LOGO_SIZE:
            return JSONResponse(status_code=400, content={"message": "Uploaded logo file is too large (max 512 KB)."})

        # Validate image integrity with Pillow
        try:
            image = Image.open(BytesIO(contents))
            image.verify()
        except Exception:
            return JSONResponse(status_code=400, content={"message": "The uploaded image file is corrupt."})

        # Save file securely
        save_path = os.path.join("static", "icons", f"logo{ext}")
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as buffer:
            buffer.write(contents)

    db.commit()

    set_debug_mode(debug_mode)

    return JSONResponse(status_code=200, content={"message": "Configuration saved successfully."})


@router.post("/reload-config")
async def reload_config():
    try:
        with open("/tmp/shared/reload_scheduler.flag", "w") as f:
            f.write("reload")
        # Mengembalikan JSONResponse yang berhasil
        return JSONResponse(status_code=200, content={"message": "Reload flag created."})
    except Exception as e:
        # Mengembalikan JSONResponse error 500
        return JSONResponse(status_code=500, content={"message": f"Failed to create reload flag: {str(e)}"})