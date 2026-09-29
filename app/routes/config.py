import os
from typing import Optional
from fastapi import APIRouter, File, Form, Request, Depends, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import pytz
from PIL import Image
from sqlalchemy.orm import Session
from app.models.config import Configuration
from app.models.user import User
from app.db.database import get_db
from app.routes.auth import admin_access_required
from app.utils.template_helper import templates
from app.core.logging_config import set_debug_mode
from app.utils.audit_logger import log_audit
from app.utils.auth import verify_password
from app.utils.notification_service import NotificationService
from app.utils.timezone_helper import clear_timezone_cache
from app.utils.email_helper import send_email
from io import BytesIO

router = APIRouter(tags=["Config"])


@router.post("/config/test-smtp")
def test_smtp_email(
    recipient_email: str = Form(...),
    current_admin: User = Depends(admin_access_required),
):
    """Send a test message using the saved SMTP configuration."""
    import re

    recipient_email = recipient_email.strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", recipient_email):
        return JSONResponse(status_code=400, content={"message": "Enter a valid recipient email address."})

    subject = "B-Snap SMTP Test Email"
    body = (
        "This is a test email from B-Snap.\n\n"
        "Your saved SMTP configuration successfully delivered this message."
    )
    html_body = (
        "<p>This is a test email from <strong>B-Snap</strong>.</p>"
        "<p>Your saved SMTP configuration successfully delivered this message.</p>"
    )

    try:
        send_email([recipient_email], subject, body, html_body)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"message": f"Failed to send test email: {exc}"},
        )

    return JSONResponse(
        status_code=200,
        content={"message": f"Test email sent successfully to {recipient_email}."},
    )


@router.get("/admin/config", response_class=HTMLResponse)
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
    
    # Check for logo file
    logo_url = None
    logo_path = os.path.join("static", "icons", "logo.png")
    ico_path = os.path.join("static", "icons", "logo.ico")
    if os.path.exists(logo_path):
        logo_url = "/static/icons/logo.png"
    elif os.path.exists(ico_path):
        logo_url = "/static/icons/logo.ico"
    
    return templates.TemplateResponse("config.html", {
        "request": request,
        "configs": config_dict,
        "timezones": timezones,
        "logo_url": logo_url
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
    snapshot_ping_check_enabled: Optional[bool] = Form(False),
    snapshot_ping_timeout_ms: int = Form(3000),
    watermark_text: str = Form(...),
    map_title: str = Form(...),
    timezone: str = Form(...),
    debug_mode: Optional[bool] = Form(False),
    retention_api_logs_days: int = Form(90),
    retention_command_logs_days: int = Form(90),
    retention_camera_stats_days: int = Form(90),
    retention_email_logs_days: int = Form(90),
    storage_critical_percent: int = Form(95),
    storage_warning_percent: int = Form(85),
    storage_info_percent: int = Form(75),
    storage_critical_free_gb: int = Form(5),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_pass: str = Form(""),
    smtp_security: str = Form("starttls"),
    email_from: str = Form(""),
    email_cc: str = Form(""),
    gowa_enabled: Optional[bool] = Form(False),
    gowa_base_url: str = Form("http://localhost:3000"),
    gowa_api_key: str = Form(""),
    gowa_device_id: str = Form(""),
    gowa_default_receiver: str = Form(""),
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
    
    # Validasi ping timeout (100ms - 10 detik)
    if snapshot_ping_timeout_ms < 100 or snapshot_ping_timeout_ms > 10000:
        return JSONResponse(
            status_code=400,
            content={"message": "Ping timeout must be between 100 and 10000 milliseconds (0.1-10 seconds)."}
        )

    # Validasi retention settings (7 hari - 10 tahun)
    retention_fields = {
        "retention_api_logs_days": retention_api_logs_days,
        "retention_command_logs_days": retention_command_logs_days,
        "retention_camera_stats_days": retention_camera_stats_days,
        "retention_email_logs_days": retention_email_logs_days,
    }
    for field_name, value in retention_fields.items():
        if value < 7 or value > 3650:
            return JSONResponse(
                status_code=400, 
                content={"message": f"{field_name} must be between 7 and 3650 days."}
            )
    
    # Validasi storage percentage thresholds (50-99%)
    for field_name, value in [("storage_critical_percent", storage_critical_percent),
                               ("storage_warning_percent", storage_warning_percent),
                               ("storage_info_percent", storage_info_percent)]:
        if value < 50 or value > 99:
            return JSONResponse(
                status_code=400,
                content={"message": f"{field_name} must be between 50 and 99."}
            )
    
    # Validasi storage critical free GB (1-100 GB)
    if storage_critical_free_gb < 1 or storage_critical_free_gb > 100:
        return JSONResponse(
            status_code=400,
            content={"message": "storage_critical_free_gb must be between 1 and 100."}
        )

    # Validasi storage thresholds
    if storage_critical_percent < storage_warning_percent:
        return JSONResponse(
            status_code=400,
            content={"message": "Critical threshold must be higher than warning threshold."}
        )
    if storage_warning_percent < storage_info_percent:
        return JSONResponse(
            status_code=400,
            content={"message": "Warning threshold must be higher than info threshold."}
        )
    
    # Validasi SMTP port (1-65535)
    if smtp_port < 1 or smtp_port > 65535:
        return JSONResponse(
            status_code=400,
            content={"message": "SMTP port must be between 1 and 65535."}
        )
    
    # Validasi email format jika diisi
    import re
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if email_from and not re.match(email_regex, email_from):
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid 'From Email' format."}
        )
    if email_cc and not re.match(email_regex, email_cc):
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid 'CC Email' format."}
        )

    if smtp_security not in {"starttls", "ssl_tls", "none"}:
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid SMTP security mode."},
        )

    if bool(smtp_user.strip()) != bool(smtp_pass):
        return JSONResponse(
            status_code=400,
            content={"message": "SMTP username and password must both be filled, or both left empty."},
        )

    gowa_base_url = gowa_base_url.strip().rstrip("/")
    if gowa_enabled:
        if not gowa_base_url.startswith(("http://", "https://")):
            return JSONResponse(
                status_code=400,
                content={"message": "GoWA Base URL must start with http:// or https://."}
            )
        if not gowa_default_receiver.strip():
            return JSONResponse(
                status_code=400,
                content={"message": "Default Receiver is required when WhatsApp notifications are enabled."}
            )

    keys = {
        "snapshot_interval_minutes": snapshot_interval_minutes,
        "healthcheck_interval_minutes": healthcheck_interval_minutes,
        "snapshot_concurrent_workers": snapshot_concurrent_workers,
        "max_screenshot_per_camera": max_screenshot_per_camera,
        "snapshot_batch_size": snapshot_batch_size,
        "snapshot_batch_delay_seconds": snapshot_batch_delay_seconds,
        "snapshot_ping_check_enabled": str(int(snapshot_ping_check_enabled)),
        "snapshot_ping_timeout_ms": snapshot_ping_timeout_ms,
        "watermark_text": watermark_text,
        "map_title": map_title,
        "timezone": timezone,
        "debug_mode": str(int(debug_mode)),
        "retention_api_logs_days": retention_api_logs_days,
        "retention_command_logs_days": retention_command_logs_days,
        "retention_camera_stats_days": retention_camera_stats_days,
        "retention_email_logs_days": retention_email_logs_days,
        "storage_critical_percent": storage_critical_percent,
        "storage_warning_percent": storage_warning_percent,
        "storage_info_percent": storage_info_percent,
        "storage_critical_free_gb": storage_critical_free_gb,
        "smtp_host": smtp_host.strip(),
        "smtp_port": smtp_port,
        "smtp_user": smtp_user.strip(),
        "smtp_pass": smtp_pass,
        "smtp_security": smtp_security,
        "email_from": email_from.strip(),
        "email_cc": email_cc.strip(),
        "gowa_enabled": str(int(gowa_enabled)),
        "gowa_base_url": gowa_base_url,
        "gowa_api_key": gowa_api_key.strip(),
        "gowa_device_id": gowa_device_id.strip(),
        "gowa_default_receiver": gowa_default_receiver.strip(),
    }

    for config_key, config_value in keys.items():
        config_entry = db.query(Configuration).filter_by(key=config_key).first()
        if config_entry:
            config_entry.value = str(config_value)
        else:
            config_entry = Configuration(key=config_key, value=str(config_value))
            db.add(config_entry)

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

    # Clear timezone cache so new timezone takes effect immediately
    clear_timezone_cache()
    
    set_debug_mode(debug_mode)
    
    # Sanitize loggers to ensure no DEBUG handlers are left behind
    # This handles cases where uvicorn/gunicorn might have added new handlers
    from app.core.logging_config import sanitize_loggers
    sanitize_loggers()
    
    # Note: Toast notification is handled by frontend

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


class CleanupResult(BaseModel):
    message: str
    details: dict


class CleanupRequest(BaseModel):
    password: str


@router.post("/config/run-cleanup", response_model=CleanupResult)
async def run_cleanup_now(
    request: Request,
    cleanup_req: CleanupRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """
    Manually trigger log cleanup jobs with current retention settings.
    Requires password verification for security.
    
    Audit logs are excluded so warm archive copies remain available in the archive viewer.
    """
    from app.jobs.scheduler import (
        delete_old_api_logs,
        delete_old_command_logs,
        delete_old_camera_stats,
    )
    from app.utils.email_notifier import cleanup_old_email_logs
    
    # Verify password
    if not verify_password(cleanup_req.password, current_admin.password):
        # Log failed attempt
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_FAILED",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"reason": "Invalid password"}
        )
        return JSONResponse(
            status_code=401,
            content={"message": "Invalid password", "details": {}}
        )
    
    results = {}
    errors = []
    
    try:
        # Run each cleanup job and capture results
        try:
            delete_old_api_logs()
            results["api_logs"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"API logs: {str(e)}")
            results["api_logs"] = f"Error: {str(e)}"
        
        try:
            delete_old_command_logs()
            results["command_logs"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"Command logs: {str(e)}")
            results["command_logs"] = f"Error: {str(e)}"
        
        try:
            delete_old_camera_stats()
            results["camera_stats"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"Camera stats: {str(e)}")
            results["camera_stats"] = f"Error: {str(e)}"
        
        try:
            deleted = cleanup_old_email_logs(db)
            results["email_logs"] = f"{deleted} records deleted"
        except Exception as e:
            errors.append(f"Email logs: {str(e)}")
            results["email_logs"] = f"Error: {str(e)}"
        
        # Log successful cleanup
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_MANUAL",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"results": results, "errors": errors}
        )
        
        if errors:
            return JSONResponse(
                status_code=500,
                content={
                    "message": "Cleanup completed with errors",
                    "details": results
                }
            )
        
        return JSONResponse(
            status_code=200,
            content={
                "message": "All cleanup jobs completed successfully",
                "details": results
            }
        )
        
    except Exception as e:
        # Log exception
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_EXCEPTION",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"error": str(e)}
        )
        return JSONResponse(
            status_code=500,
            content={
                "message": f"Cleanup failed: {str(e)}",
                "details": results
            }
        )
