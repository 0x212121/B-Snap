"""Canonical documentation routes, static references and compatibility redirects."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.routes.auth import admin_access_required
from app.utils.template_helper import templates

router = APIRouter(tags=["Documentation"])

# Environment Variables Documentation
ENV_DOCS: dict[str, dict[str, Any]] = {
    "core": {
        "title": "Core Application Settings",
        "description": "Required settings for basic application functionality",
        "vars": [
            {
                "name": "SECRET_KEY",
                "required": True,
                "default": "your-default-secret-key-for-dev",
                "description": "Secret key for session management, CSRF protection, and signed tokens",
                "security": "CRITICAL - Change in production!",
                "used_in": ["app/main.py", "app/routes/auth.py", "app/routes/snapshots.py"],
                "recommendation": "Generate with: openssl rand -hex 32",
            },
            {
                "name": "ENCRYPTION_KEY",
                "required": True,
                "default": None,
                "description": "AES-256-GCM encryption key for sensitive data (camera passwords)",
                "security": "CRITICAL - Keep secure and backed up!",
                "used_in": ["app/utils/encryption.py", "app/jobs/audit_archive.py"],
                "recommendation": "Generate with: openssl rand -hex 32",
            },
            {
                "name": "ENVIRONMENT",
                "required": True,
                "default": "development",
                "description": "Application environment mode",
                "security": "Affects cookie security and debug mode",
                "used_in": ["app/main.py", "app/routes/auth.py", "app/utils/remember_me.py"],
                "options": ["development", "testing", "production"],
                "recommendation": "Set to 'production' for production deployments",
            },
            {
                "name": "DATABASE_URL",
                "required": True,
                "default": None,
                "description": "PostgreSQL database connection URL",
                "format": "postgresql+psycopg2://user:password@host:port/database",
                "used_in": ["app/db/database.py", "app/ws/routes.py", "app/ws/notifier.py"],
                "recommendation": "Use connection pooling for production",
            },
        ],
    },
    "session": {
        "title": "Session & Authentication",
        "description": "Session management and authentication settings",
        "vars": [
            {
                "name": "SESSION_MAX_AGE_SECONDS",
                "required": False,
                "default": "86400 (24 hours)",
                "description": "Session token expiration time in seconds",
                "used_in": ["app/routes/auth.py", "app/middleware/auth_and_setup.py"],
                "recommendation": "Keep at 24 hours. For CCTV 24/7, use Remember Me feature instead of extending this",
            },
            {
                "name": "MAX_WEB_SESSIONS",
                "required": False,
                "default": "1",
                "description": "Maximum concurrent web sessions per user",
                "used_in": ["app/routes/auth.py"],
                "recommendation": "Increase only if users need multiple active browser sessions",
            },
        ],
    },
    "security": {
        "title": "Security Settings",
        "description": "Security-related configuration",
        "vars": [
            {
                "name": "TRUSTED_PROXY_HOSTS",
                "required": False,
                "default": "Loopback in development; empty in production",
                "description": "Comma-separated trusted proxy peer IPs/CIDRs; required for production behind an HTTPS proxy",
                "security": "Trust only the directly connected proxy/connector; production rejects wildcard networks",
                "used_in": ["app/core/security.py", "app/main.py"],
                "recommendation": "Use actual cloudflared/proxy peer IPs, not domains. Legacy TRUSTED_HOSTS is only a development/testing fallback.",
            },
            {
                "name": "PUBLIC_ORIGIN",
                "required": False,
                "default": None,
                "description": "Canonical browser origin for CSRF and WebSocket validation when proxies rewrite Host/scheme/port",
                "recommendation": "Use the public HTTPS origin, for example https://camera.example.com, without a path.",
            },
            {
                "name": "BEHIND_HTTPS_PROXY",
                "required": False,
                "default": "false",
                "description": "Application is behind an HTTPS-terminating proxy; production requires explicit TRUSTED_PROXY_HOSTS when enabled",
            },
            {
                "name": "COOKIE_SECURE",
                "required": False,
                "default": "true in production or behind an HTTPS proxy; otherwise false",
                "description": "Only send login cookies over HTTPS; production rejects false",
            },
            {
                "name": "COOKIE_SAMESITE",
                "required": False,
                "default": "lax",
                "description": "Cookie SameSite policy: lax, strict, or none; none requires Secure cookies",
            },
            {
                "name": "DEBUG",
                "required": False,
                "default": "false",
                "description": "Debug mode flag",
                "security": "NEVER enable in production!",
                "used_in": ["app/main.py"],
                "recommendation": "Always false in production",
            },
        ],
    },
    "email": {
        "title": "Email Configuration (SMTP)",
        "description": "SMTP server settings for email notifications",
        "vars": [
            {
                "name": "SMTP_HOST",
                "required": False,
                "default": "smtp.gmail.com",
                "description": "SMTP server hostname",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "SMTP_PORT",
                "required": False,
                "default": "587",
                "description": "SMTP server port",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "SMTP_USER",
                "required": False,
                "default": None,
                "description": "SMTP authentication username",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "SMTP_PASSWORD",
                "required": False,
                "default": None,
                "description": "SMTP authentication password or app password",
                "security": "Keep secure!",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "SMTP_TLS",
                "required": False,
                "default": "true",
                "description": "Enable TLS encryption",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "EMAIL_FROM",
                "required": False,
                "default": None,
                "description": "Default sender email address",
                "used_in": ["app/utils/smtp_config.py"],
            },
            {
                "name": "EMAIL_CC",
                "required": False,
                "default": None,
                "description": "Default CC email addresses (comma-separated)",
                "used_in": ["app/utils/smtp_config.py"],
            },
        ],
    },
    "retention": {
        "title": "Retention & Cleanup (MED-003)",
        "description": "Automated retention policy settings",
        "vars": [
            {
                "name": "RETENTION_SNAPSHOT_DAYS",
                "required": False,
                "default": "30",
                "description": "Snapshots older than this are soft-deleted",
                "used_in": ["app/jobs/scheduler.py"],
                "recommendation": "Adjust based on storage capacity. Items with retention_hold=True are preserved",
            },
            {
                "name": "RETENTION_VIDEO_DAYS",
                "required": False,
                "default": "7",
                "description": "Videos older than this are soft-deleted",
                "used_in": ["app/jobs/scheduler.py"],
                "recommendation": "Videos take more space, shorter retention recommended",
            },
        ],
    },
    "camera": {
        "title": "Camera & Monitoring",
        "description": "Camera monitoring and alert settings",
        "vars": [
            {
                "name": "OFFLINE_ALERT_THRESHOLD_SECONDS",
                "required": False,
                "default": "1800 (30 minutes)",
                "description": "Camera considered offline after this many seconds",
                "used_in": ["app/utils/healthcheck.py"],
                "recommendation": "Lower for critical cameras, higher for non-critical",
            },
            {
                "name": "CAMERA_TIMEOUT",
                "required": False,
                "default": "10",
                "description": "Default timeout for camera snapshot operations (seconds)",
            },
        ],
    },
    "record_mounts": {
        "title": "Native Recording Mounts",
        "description": "Opt-in Linux SMB/NFS mounts; see docs/source/record-mounts.md for deployment prerequisites",
        "vars": [
            {
                "name": "BSNAP_RECORD_MOUNTS_ENABLED",
                "required": False,
                "default": "false",
                "description": "Enable native read-only SMB/NFS recording mounts (Linux with mount privileges only)",
                "used_in": ["app/utils/record_mount.py"],
            },
            {
                "name": "BSNAP_RECORD_MOUNT_ALLOWED_NETWORKS",
                "required": False,
                "default": "",
                "description": "Required comma-separated IP/CIDR allowlist for native mounts; empty denies all servers",
                "recommendation": "Use individual trusted storage IPs, for example 192.168.10.20/32",
            },
            {
                "name": "BSNAP_RECORD_NFS_ALLOW_SYS",
                "required": False,
                "default": "false",
                "description": "Allow unencrypted NFS AUTH_SYS instead of Kerberos encryption; isolated trusted networks only",
            },
        ],
    },
    "whatsapp": {
        "title": "WhatsApp Gateway",
        "description": "Timeout and retry limits for messages sent through GoWA",
        "vars": [
            {
                "name": "GOWA_SEND_TIMEOUT_SECONDS",
                "required": False,
                "default": "10",
                "description": "Timeout for each outgoing GoWA message request (1 to 120 seconds)",
                "used_in": ["app/utils/wa_gateway.py"],
            },
            {
                "name": "GOWA_SEND_RETRIES",
                "required": False,
                "default": "2",
                "description": "Retry count for outgoing GoWA messages after a timeout, connection failure, or server error (0 to 5)",
                "used_in": ["app/utils/wa_gateway.py"],
            },
            {
                "name": "WA_CAMERA_THREAD_WORKERS",
                "required": False,
                "default": "4",
                "description": "Maximum concurrent WhatsApp camera captures per web worker process (1 to 16)",
                "used_in": ["app/utils/wa_executor.py"],
            },
            {
                "name": "WA_GATEWAY_THREAD_WORKERS",
                "required": False,
                "default": "8",
                "description": "Maximum concurrent blocking GoWA sends per web worker process (1 to 32)",
                "used_in": ["app/utils/wa_executor.py"],
            },
        ],
    },
    "logging": {
        "title": "Logging Configuration",
        "description": "Logging and monitoring settings",
        "vars": [
            {
                "name": "LOG_LEVEL",
                "required": False,
                "default": "INFO",
                "description": "Logging verbosity level",
                "options": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                "recommendation": "Use INFO for production, DEBUG for troubleshooting",
            },
            {
                "name": "LOG_DIR",
                "required": False,
                "default": "./logs",
                "description": "Directory for log files",
                "used_in": ["app/core/logging_config.py"],
            },
            {
                "name": "BSNAP_LOG_VIA_GUNICORN",
                "required": False,
                "default": "1",
                "description": "Route logs through gunicorn",
                "used_in": ["app/main.py"],
                "recommendation": "Set to 0 for direct logging during development",
            },
        ],
    },
    "paths": {
        "title": "Storage Paths",
        "description": "File system paths for storage",
        "vars": [
            {
                "name": "SNAPSHOT_PATH",
                "required": False,
                "default": "./static/snapshots",
                "description": "Directory for snapshot storage",
            },
            {
                "name": "VIDEO_PATH",
                "required": False,
                "default": "./static/videos",
                "description": "Directory for video storage",
            },
            {
                "name": "ALEMBIC_INI_PATH",
                "required": False,
                "default": "./alembic.ini",
                "description": "Path to Alembic configuration file",
                "used_in": ["app/main.py"],
            },
        ],
    },
    "server": {
        "title": "Server & Performance",
        "description": "Server configuration and performance tuning",
        "vars": [
            {
                "name": "WORKERS",
                "required": False,
                "default": "2",
                "description": "Number of gunicorn worker processes",
                "recommendation": "production: 2-4 x CPU cores",
            },
            {"name": "PORT", "required": False, "default": "8080", "description": "Server port"},
            {
                "name": "BIND",
                "required": False,
                "default": "0.0.0.0:8080",
                "description": "Server bind address",
            },
        ],
    },
    "timezone": {
        "title": "Timezone & Scheduling",
        "description": "Timezone and scheduler settings",
        "vars": [
            {
                "name": "TZ",
                "required": False,
                "default": "Asia/Singapore",
                "description": "Process timezone; application displays and scheduler triggers use the database timezone setting",
            },
            {
                "name": "SCHEDULER_ENABLED",
                "required": False,
                "default": "true",
                "description": "Enable scheduled snapshot jobs",
            },
        ],
    },
}


@router.get("/docs", include_in_schema=False)
async def get_docs() -> RedirectResponse:
    """Keep the bookmarked entry point while using one canonical environment URL."""
    return RedirectResponse(url="/docs/environment", status_code=307)


@router.get(
    "/docs/openapi",
    response_class=HTMLResponse,
    include_in_schema=False,
    dependencies=[Depends(admin_access_required)],
)
async def get_openapi_docs(request: Request) -> HTMLResponse:
    """Render the existing embedded Swagger UI with current administrative access."""
    return templates.TemplateResponse("swagger_embed.html", {"request": request})


@router.get(
    "/developer/docs",
    include_in_schema=False,
    name="embedded_docs",
    dependencies=[Depends(admin_access_required)],
)
async def developer_docs_alias() -> RedirectResponse:
    """Preserve the old administrative Swagger bookmark."""
    return RedirectResponse(url="/docs/openapi", status_code=307)


@router.get("/docs/openapi.json", include_in_schema=False)
async def openapi_schema_alias() -> RedirectResponse:
    """Preserve the schema URL published in older documentation."""
    return RedirectResponse(url="/openapi.json", status_code=307)


@router.get("/docs/environment", response_class=HTMLResponse)
async def get_env_docs(request: Request) -> HTMLResponse:
    """Render the environment reference from static, non-secret metadata."""
    return templates.TemplateResponse(
        "docs/environment.html",
        {
            "request": request,
            "title": "Environment Variables Documentation",
            "env_docs": ENV_DOCS,
            "total_vars": sum(len(section["vars"]) for section in ENV_DOCS.values()),
        },
    )


@router.get("/docs/api", response_class=HTMLResponse)
async def get_api_docs(request: Request) -> HTMLResponse:
    """Render the API reference and canonical Swagger link."""
    return templates.TemplateResponse(
        "docs/api.html", {"request": request, "title": "API Documentation"}
    )


@router.get("/docs/security", response_class=HTMLResponse)
async def get_security_docs(request: Request) -> HTMLResponse:
    """Render the existing security reference."""
    return templates.TemplateResponse(
        "docs/security.html", {"request": request, "title": "Security Documentation"}
    )
