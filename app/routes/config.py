import os
import re

from io import BytesIO
from typing import Annotated

import pytz

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.logging_config import set_debug_mode
from app.db.database import get_db
from app.models.config import Configuration
from app.models.user import User
from app.routes.auth import admin_access_required
from app.utils.audit_logger import log_audit
from app.utils.auth import verify_password
from app.utils.email_helper import send_email
from app.utils.template_helper import templates
from app.utils.timezone_helper import clear_timezone_cache
from app.utils.wa_monitor import parse_monitor_recipients

router = APIRouter(tags=["Config"])


@router.post("/config/test-smtp")
def test_smtp_email(
    recipient_email: Annotated[str, Form()],
    # This is an HTTP endpoint, not a pytest test with an unused fixture.
    _current_admin: Annotated[User, Depends(admin_access_required)],  # noqa: PT019
) -> JSONResponse:
    """Send a test message using the saved SMTP configuration."""
    recipient_email = recipient_email.strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", recipient_email):
        return JSONResponse(
            status_code=400, content={"message": "Enter a valid recipient email address."}
        )

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
    db: Annotated[Session, Depends(get_db)],
    _current_admin: Annotated[User, Depends(admin_access_required)],
) -> HTMLResponse:
    """Render application configuration for an authenticated administrator."""
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

    return templates.TemplateResponse(
        "config.html",
        {"request": request, "configs": config_dict, "timezones": timezones, "logo_url": logo_url},
    )


MAX_LOGO_SIZE = 512 * 1024  # 512 KB
ALLOWED_EXTENSIONS = {".png", ".ico"}
ALLOWED_MIME_TYPES = {"image/png", "image/x-icon"}


@router.post("/config/save")
# Keep the existing validation order and individual error responses in this form handler.
async def config_save(  # noqa: C901, PLR0911, PLR0912
    *,
    _request: Request,
    db: Annotated[Session, Depends(get_db)],
    _current_admin: Annotated[User, Depends(admin_access_required)],
    healthcheck_interval_minutes: Annotated[int, Form()],
    snapshot_concurrent_workers: Annotated[int, Form()],
    max_screenshot_per_camera: Annotated[int, Form()],
    snapshot_batch_size: Annotated[int, Form()],
    snapshot_batch_delay_seconds: Annotated[int, Form()],
    snapshot_ping_check_enabled: Annotated[bool | None, Form()] = False,
    snapshot_ping_timeout_ms: Annotated[int, Form()] = 3000,
    watermark_text: Annotated[str, Form()],
    map_title: Annotated[str, Form()],
    timezone: Annotated[str, Form()],
    debug_mode: Annotated[bool | None, Form()] = False,
    retention_api_logs_days: Annotated[int, Form()] = 90,
    retention_command_logs_days: Annotated[int, Form()] = 90,
    retention_camera_stats_days: Annotated[int, Form()] = 90,
    retention_email_logs_days: Annotated[int, Form()] = 90,
    storage_critical_percent: Annotated[int, Form()] = 95,
    storage_warning_percent: Annotated[int, Form()] = 85,
    storage_info_percent: Annotated[int, Form()] = 75,
    storage_critical_free_gb: Annotated[int, Form()] = 5,
    smtp_host: Annotated[str, Form()] = "",
    smtp_port: Annotated[int, Form()] = 587,
    smtp_user: Annotated[str, Form()] = "",
    smtp_pass: Annotated[str, Form()] = "",
    smtp_security: Annotated[str, Form()] = "starttls",
    email_from: Annotated[str, Form()] = "",
    email_cc: Annotated[str, Form()] = "",
    email_retry_max_pending_hours: Annotated[int | None, Form(ge=1, le=720)] = None,
    email_retry_batch_size: Annotated[int | None, Form(ge=1, le=500)] = None,
    email_retry_max_run_seconds: Annotated[int | None, Form(ge=1, le=3600)] = None,
    email_retry_smtp_timeout_seconds: Annotated[int | None, Form(ge=1, le=30)] = None,
    gowa_enabled: Annotated[bool | None, Form()] = False,
    gowa_base_url: Annotated[str, Form()] = "http://localhost:3000",
    gowa_api_key: Annotated[str, Form()] = "",
    gowa_device_id: Annotated[str, Form()] = "",
    gowa_default_receiver: Annotated[str, Form()] = "",
    app_logo: Annotated[UploadFile | None, File()] = None,
    gowa_monitor_enabled: Annotated[bool, Form()] = False,
    gowa_monitor_recipients: Annotated[str, Form(max_length=2000)] = "",
    gowa_monitor_interval_minutes: Annotated[int, Form(ge=1, le=1440)] = 1,
) -> JSONResponse:
    """Validate and save settings while preserving omitted optional resource budgets."""
    # Validasi timezone
    if timezone not in pytz.all_timezones:
        # Mengembalikan JSONResponse dengan status 400 Bad Request
        return JSONResponse(status_code=400, content={"message": "Invalid timezone selected."})

    # Validasi batch size
    if snapshot_batch_size > 150 or snapshot_batch_size < 1:
        # Mengembalikan JSONResponse dengan status 400 Bad Request
        return JSONResponse(
            status_code=400, content={"message": "Snapshot batch size must be between 1 and 150."}
        )

    # Validasi ping timeout (100ms - 10 detik)
    if snapshot_ping_timeout_ms < 100 or snapshot_ping_timeout_ms > 10000:
        return JSONResponse(
            status_code=400,
            content={
                "message": "Ping timeout must be between 100 and 10000 milliseconds (0.1-10 seconds)."
            },
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
                content={"message": f"{field_name} must be between 7 and 3650 days."},
            )

    # Validasi storage percentage thresholds (50-99%)
    for field_name, value in [
        ("storage_critical_percent", storage_critical_percent),
        ("storage_warning_percent", storage_warning_percent),
        ("storage_info_percent", storage_info_percent),
    ]:
        if value < 50 or value > 99:
            return JSONResponse(
                status_code=400, content={"message": f"{field_name} must be between 50 and 99."}
            )

    # Validasi storage critical free GB (1-100 GB)
    if storage_critical_free_gb < 1 or storage_critical_free_gb > 100:
        return JSONResponse(
            status_code=400,
            content={"message": "storage_critical_free_gb must be between 1 and 100."},
        )

    # Validasi storage thresholds
    if storage_critical_percent < storage_warning_percent:
        return JSONResponse(
            status_code=400,
            content={"message": "Critical threshold must be higher than warning threshold."},
        )
    if storage_warning_percent < storage_info_percent:
        return JSONResponse(
            status_code=400,
            content={"message": "Warning threshold must be higher than info threshold."},
        )

    # Validasi SMTP port (1-65535)
    if smtp_port < 1 or smtp_port > 65535:
        return JSONResponse(
            status_code=400, content={"message": "SMTP port must be between 1 and 65535."}
        )

    # Validasi email format jika diisi
    email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if email_from and not re.match(email_regex, email_from):
        return JSONResponse(status_code=400, content={"message": "Invalid 'From Email' format."})
    if email_cc and not re.match(email_regex, email_cc):
        return JSONResponse(status_code=400, content={"message": "Invalid 'CC Email' format."})

    if smtp_security not in {"starttls", "ssl_tls", "none"}:
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid SMTP security mode."},
        )

    if bool(smtp_user.strip()) != bool(smtp_pass):
        return JSONResponse(
            status_code=400,
            content={
                "message": "SMTP username and password must both be filled, or both left empty."
            },
        )

    gowa_base_url = gowa_base_url.strip().rstrip("/")
    if gowa_enabled:
        if not gowa_base_url.startswith(("http://", "https://")):
            return JSONResponse(
                status_code=400,
                content={"message": "GoWA Base URL must start with http:// or https://."},
            )
        if not gowa_default_receiver.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "message": "Default Receiver is required when WhatsApp notifications are enabled."
                },
            )

    try:
        monitor_recipients = parse_monitor_recipients(
            gowa_monitor_recipients, enabled=gowa_monitor_enabled, gateway_enabled=gowa_enabled
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"message": str(exc)})

    keys = {
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
        "gowa_monitor_enabled": str(int(gowa_monitor_enabled)),
        "gowa_monitor_recipients": ", ".join(monitor_recipients),
        "gowa_monitor_interval_minutes": gowa_monitor_interval_minutes,
    }

    # Older clients can omit these fields without overwriting saved resource budgets.
    keys.update(
        {
            key: value
            for key, value in {
                "email_retry_max_pending_hours": email_retry_max_pending_hours,
                "email_retry_batch_size": email_retry_batch_size,
                "email_retry_max_run_seconds": email_retry_max_run_seconds,
                "email_retry_smtp_timeout_seconds": email_retry_smtp_timeout_seconds,
            }.items()
            if value is not None
        }
    )

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
            return JSONResponse(
                status_code=400,
                content={"message": "Invalid logo file format. Only .png or .ico are allowed."},
            )

        # Read file content
        contents = await app_logo.read()
        if len(contents) > MAX_LOGO_SIZE:
            return JSONResponse(
                status_code=400,
                content={"message": "Uploaded logo file is too large (max 512 KB)."},
            )

        # Validate image integrity with Pillow
        try:
            image = Image.open(BytesIO(contents))
            image.verify()
        except Exception:
            return JSONResponse(
                status_code=400, content={"message": "The uploaded image file is corrupt."}
            )

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
    # Resolve logger sanitization at call time after updating debug mode.
    from app.core.logging_config import sanitize_loggers  # noqa: PLC0415

    sanitize_loggers()

    # Note: Toast notification is handled by frontend

    return JSONResponse(status_code=200, content={"message": "Configuration saved successfully."})


@router.post("/reload-config")
async def reload_config() -> JSONResponse:
    """Request a scheduler configuration reload through the shared flag file."""
    try:
        with open("/tmp/shared/reload_scheduler.flag", "w") as f:
            f.write("reload")
        # Mengembalikan JSONResponse yang berhasil
        return JSONResponse(status_code=200, content={"message": "Reload flag created."})
    except Exception as e:
        # Mengembalikan JSONResponse error 500
        return JSONResponse(
            status_code=500, content={"message": f"Failed to create reload flag: {e!s}"}
        )


class CleanupResult(BaseModel):
    """Summarize the outcome of a manual log cleanup."""

    message: str
    details: dict


class CleanupRequest(BaseModel):
    """Require password confirmation before manual log cleanup."""

    password: str


@router.post("/config/run-cleanup", response_model=CleanupResult)
async def run_cleanup_now(
    request: Request,
    cleanup_req: CleanupRequest,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(admin_access_required)],
) -> JSONResponse:
    """
    Manually trigger log cleanup jobs with current retention settings.

    Requires password verification for security.

    Audit logs are excluded so warm archive copies remain available in the archive viewer.
    """
    # Load scheduler jobs only for manual cleanup to avoid eager scheduler imports.
    from app.jobs.scheduler import (  # noqa: PLC0415
        delete_old_api_logs,
        delete_old_camera_stats,
        delete_old_command_logs,
    )
    from app.utils.email_notifier import cleanup_old_email_logs  # noqa: PLC0415

    # Verify password
    if not verify_password(cleanup_req.password, current_admin.password):
        # Log failed attempt
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_FAILED",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"reason": "Invalid password"},
        )
        return JSONResponse(status_code=401, content={"message": "Invalid password", "details": {}})

    results = {}
    errors = []

    try:
        # Run each cleanup job and capture results
        try:
            delete_old_api_logs()
            results["api_logs"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"API logs: {e!s}")
            results["api_logs"] = f"Error: {e!s}"

        try:
            delete_old_command_logs()
            results["command_logs"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"Command logs: {e!s}")
            results["command_logs"] = f"Error: {e!s}"

        try:
            delete_old_camera_stats()
            results["camera_stats"] = "Cleaned successfully"
        except Exception as e:
            errors.append(f"Camera stats: {e!s}")
            results["camera_stats"] = f"Error: {e!s}"

        try:
            deleted = cleanup_old_email_logs(db)
            results["email_logs"] = f"{deleted} records deleted"
        except Exception as e:
            errors.append(f"Email logs: {e!s}")
            results["email_logs"] = f"Error: {e!s}"

        # Log successful cleanup
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_MANUAL",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"results": results, "errors": errors},
        )

        if errors:
            return JSONResponse(
                status_code=500,
                content={"message": "Cleanup completed with errors", "details": results},
            )

        return JSONResponse(
            status_code=200,
            content={"message": "All cleanup jobs completed successfully", "details": results},
        )

    except Exception as e:
        # Log exception
        log_audit(
            db=db,
            user=current_admin.username,
            action="CLEANUP_EXCEPTION",
            target="log_cleanup",
            ip=request.client.host if request.client else None,
            extra={"error": str(e)},
        )
        return JSONResponse(
            status_code=500, content={"message": f"Cleanup failed: {e!s}", "details": results}
        )
