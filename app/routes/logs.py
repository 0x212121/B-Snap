from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.models_sql import User
from app.routes.auth import operator_access_required
from app.utils.template_helper import templates
from pathlib import Path
import os

router = APIRouter()

LOG_DIR = Path("logs")  # Pastikan ini path absolut atau relatif yang aman
LOG_TYPES = ["main", "snapshot", "healthcheck", "scheduler", "management"]

# ─── 1. UI view ─────────────────────────────────────────────────────
@router.get("/logs", response_class=HTMLResponse)
async def view_logs(
    request: Request,
    log_type: str = "main",
    current_operator: User = Depends(operator_access_required)
):
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "log_type": log_type,
    })


# ─── 2. Return list of log files under that log_type ────────────────
@router.get("/logs/files/{log_type}")
async def list_log_files(
    log_type: str,
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    dir_path = LOG_DIR
    if not dir_path.exists():
        return JSONResponse(content={"files": []})

    # List only .log, .log.1, .log.2 etc.
    files = sorted([
        f.name for f in dir_path.glob(f"{log_type}.log*") if f.is_file()
    ])
    return JSONResponse(content={"files": files})


# ─── 3. Return log content ──────────────────────────────────────────
@router.get("/logs/content/{log_type}/{filename}")
async def get_log_content(
    log_type: str,
    filename: str,
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    safe_filename = os.path.basename(filename)
    log_path = LOG_DIR / safe_filename

    if not log_path.exists() or not log_path.is_file():
        return JSONResponse(content={"lines": [f"{filename} not found."]})

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-1000:]
        return JSONResponse(content={"lines": lines})
    except Exception as e:
        return JSONResponse(content={"lines": [f"Error: {e}"]})


# ─── 4. Download a specific log file ────────────────────────────────
@router.get("/logs/download/{log_type}/{filename}")
async def download_log(
    log_type: str,
    filename: str,
    current_operator: User = Depends(operator_access_required)
):
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="Invalid log type")

    safe_filename = os.path.basename(filename)
    log_path = LOG_DIR / safe_filename

    if not log_path.exists() or not log_path.is_file():
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(log_path, filename=safe_filename, media_type="text/plain")
