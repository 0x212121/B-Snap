from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.models_sql import User
from app.routes.auth import operator_access_required
import os

router = APIRouter()

from app.utils.template_helper import templates
LOG_DIR = "logs"


@router.get("/logs", response_class=HTMLResponse)
async def view_logs(request: Request, log_type: str = "main", current_operator: User = Depends(operator_access_required)):
    log_map = {
        "main": "logs/main.log",
        "snapshot": "logs/snapshot.log",
        "healthcheck": "logs/healthcheck.log",
        "scheduler": "logs/scheduler.log",
        "management": "logs/management.log"
    }
    log_path = log_map.get(log_type, "logs/main.log")

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-300:]  # tampilkan 300 baris terakhir
    except Exception as e:
        lines = [f"Error reading log file: {e}"]

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "log_type": log_type,
        "log_content": lines,
    })


@router.get("/logs/download/{log_type}")
async def download_log(log_type: str, current_operator: User = Depends(operator_access_required)):
    log_file = os.path.join(LOG_DIR, f"{log_type}.log")
    if not os.path.exists(log_file):
        return {"error": "Log file not found"}
    return FileResponse(log_file, filename=f"{log_type}.log", media_type='text/plain')


@router.get("/logs/content/{log_type}")
async def get_log_content(log_type: str, current_operator: User = Depends(operator_access_required)):
    log_files = {
        "main": "main.log",
        "snapshot": "snapshot.log",
        "healthcheck": "healthcheck.log",
        "scheduler": "scheduler.log",
        "management": "management.log"
    }
    log_file = log_files.get(log_type, "main.log")
    log_path = os.path.join(LOG_DIR, log_file)

    if not os.path.exists(log_path):
        return JSONResponse(content={"lines": ["Log file not found."]})

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()[-500:]  # Batasi jumlah baris
    return JSONResponse(content={"lines": lines})