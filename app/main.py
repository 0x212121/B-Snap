# ====================================================================
# 1. IMPORTS
# ====================================================================
import asyncio
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
import traceback

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import ORJSONResponse, RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from sqlalchemy import inspect

from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware import Middleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from app.middleware.http_proxy_fix import HTTPSProxyFixMiddleware
from app.middleware.observability import ObservabilityMiddleware

from app.middleware.auth_and_setup import AuthAndSetupMiddleware
from app.middleware.session_restore import RestoreSessionMiddleware
from app.middleware.online_user_tracker import OnlineUserTrackerMiddleware

from app.db.migrate import require_current_schema
from app.core.logging_config import setup_logging
from app.core.static_assets import mount_documentation
from app.db.database import engine, SessionLocal
from app.utils.timezone_helper import clear_timezone_cache

# Register all models without performing schema changes during web startup.
import app.models  # noqa: F401 - imports all models via __init__.py
from app.routes import (
    admin, auth, audit, audit_log, cameras, camera_groups, config, dev_docs, docs, health, jobs, logs, maps,
    nvrs, ping, resolve_ip, setup, snap_gallery, snapshots, stats,
    user_management, user_profile, videos, whitelist, group_recipients, email_logs, wa_webhook,
    notifications, toast_demo, email_templates, online_users, record_checks
)
from app.routes import insights
from app.ws.routes import notification_listener, router as ws_router
from app.version import __version__
from app.api import whatsapp_routes
from app.api import observability_log
from app.api.readiness import router as readiness_router
from app.ws.manager import websocket_connections
from functools import lru_cache

from app.utils.template_helper import templates

# ====================================================================
# 2. INITIAL SETUP & CONFIGURATION
# ====================================================================
if os.getenv("BSNAP_LOG_VIA_GUNICORN", "1") not in ("1", "true", "yes"):
    setup_logging()

logger = logging.getLogger("main")

# Environment detection
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

SECRET_KEY = os.getenv("SECRET_KEY", "your-default-secret-key-for-dev")
if SECRET_KEY == "your-default-secret-key-for-dev":
    logger.warning("Using default SECRET_KEY. This is not secure for production.")
    

# FIX: parse TRUSTED_HOSTS string → set
_raw_hosts = os.getenv("TRUSTED_HOSTS", "*")
if _raw_hosts.strip() == "*":
    TRUSTED_HOSTS = {"*"}
else:
    TRUSTED_HOSTS = {h.strip() for h in _raw_hosts.split(",") if h.strip()}

# B-snap version ke template globals
templates.env.globals["version"] = __version__

# ====================================================================
# 3. APPLICATION LIFESPAN (STARTUP & SHUTDOWN)
# ====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ready = False
    # Log startup environment
    env_display = ENVIRONMENT.upper()
    if ENVIRONMENT == "production":
        logger.info(f"🚀 B-Snap starting in {env_display} mode")
    elif ENVIRONMENT == "testing":
        logger.info(f"🧪 B-Snap starting in {env_display} mode")
    else:
        logger.info(f"🔧 B-Snap starting in {env_display} mode")
    
    logger.info("Lifespan startup: Initializing database...")
    
    # Wait a bit for database to be fully ready in containerized environments
    max_retries = 5
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            logger.info(f"Database connected. Found tables: {tables}")
            break
        except Exception as e:
            logger.warning(f"Database not ready (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
            else:
                logger.error("Database connection failed after all retries")
                raise

    # Clear timezone cache on startup to ensure fresh config is loaded
    clear_timezone_cache()
    logger.info("Timezone cache cleared on startup.")
    
    # Workers only validate readiness; schema changes and seeding belong to migrate.
    require_current_schema()

    db = SessionLocal()
    try:
        from app.models.config import Configuration
        from app.core.logging_config import set_debug_mode

        debug_config = db.query(Configuration).filter_by(key="debug_mode").first()
        set_debug_mode(bool(debug_config and debug_config.value == "1"))
    except Exception as e:
        logger.warning(f"Could not load debug mode from database: {e}")
    finally:
        db.close()

    # Start WS notification listener
    try:
        task = asyncio.create_task(notification_listener(websocket_connections))
        app.state.notification_listener_task = task
        logger.info("WebSocket notification listener started.")
    except Exception as e:
        logger.error(f"Failed to start notification listener: {e}")
        # Create a dummy task so the finally block doesn't fail
        app.state.notification_listener_task = None

    try:
        logger.info("Application startup complete.")
        app.state.ready = True
        yield
    finally:
        app.state.ready = False
        if app.state.notification_listener_task:
            app.state.notification_listener_task.cancel()
            try:
                await app.state.notification_listener_task
            except asyncio.CancelledError:
                logger.info("Notification listener task successfully cancelled.")

# ====================================================================
# 4. FASTAPI APP INSTANCE & MIDDLEWARE
# ====================================================================
from app.middleware.real_ip_fix import RealIPFixMiddleware

middleware = [
    Middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_HOSTS),
    Middleware(HTTPSProxyFixMiddleware),
    Middleware(RealIPFixMiddleware),
    Middleware(GZipMiddleware, minimum_size=1000),
    Middleware(
        SessionMiddleware, 
        secret_key=SECRET_KEY, 
        max_age=86400,  # FIX: 24 jam sama seperti SESSION_MAX_AGE_SECONDS di auth.py
        path="/",       # FIX: Penting! Pastikan cookie berlaku di seluruh path
        same_site="lax",
        https_only=False  # True jika HTTPS
    ),
    Middleware(OnlineUserTrackerMiddleware),
    Middleware(AuthAndSetupMiddleware),
]

app = FastAPI(
    lifespan=lifespan,
    title="B-Snap API",
    description="B-Snap Documentation API",
    version="1.0",
    docs_url=None,
    redoc_url=None,
    middleware=middleware,
    default_response_class=ORJSONResponse,
)

# FIX: Hapus middleware yang tidak mengembalikan response
# @app.middleware("http")
# async def catch_exceptions_middleware(request: Request, call_next):
#     log = logging.getLogger("main")
#     log.warning(f"[MIDDLEWARE TEST] entered for {request.url}")
#     # BUG: tidak memanggil call_next dan tidak mengembalikan Response

# Tambahkan ServerErrorMiddleware lebih awal agar exceptions ditangani ke 500
app.add_middleware(ServerErrorMiddleware, handler=None)
app.add_middleware(ObservabilityMiddleware)

# FIX: Satu middleware error wrapper yang benar-benar meneruskan request
@app.middleware("http")
async def error_wrapper_middleware(request: Request, call_next):
    logger = logging.getLogger("main")
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            logger.error(f"HTTP {response.status_code} at {request.url}")
            # Keep API error bodies machine-readable for AJAX clients.
            if "application/json" in request.headers.get("accept", "").lower():
                return response
            return templates.TemplateResponse("500.html", {"request": request}, status_code=500)
        return response
    except Exception as exc:
        error_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(f"Unhandled error at {request.url}:\n{error_trace}")
        return templates.TemplateResponse("500.html", {"request": request}, status_code=500)

# ====================================================================
# 6. ROUTERS & STATIC FILES
# ====================================================================

# P2-004: Block direct access to /static/snapshots - MUST be before StaticFiles mount
@app.get("/static/snapshots/{path:path}", include_in_schema=False)
async def block_snapshot_access(request: Request, path: str):
    """Block direct access to snapshot files (P2-004).
    
    Snapshots must be accessed via authenticated API:
    - GET /api/snapshots/secure/{snapshot_id}
    - GET /api/snapshots/file/{file_path}
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=403,
        content={
            "status": "error",
            "detail": "Direct snapshot access blocked (P2-004). Use authenticated API endpoints.",
            "code": "SNAPSHOT_ACCESS_BLOCKED",
            "help": "Access snapshots via: /api/snapshots/secure/{snapshot_id}"
        }
    )

# CRIT-001: Block direct access to /static/videos - MUST be before StaticFiles mount
@app.get("/static/videos/{path:path}", include_in_schema=False)
async def block_video_access(request: Request, path: str):
    """Block direct access to video files (CRIT-001).
    
    Videos must be accessed via authenticated API:
    - GET /api/videos/secure/{video_id}
    - GET /api/videos/file/{file_path}
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=403,
        content={
            "status": "error",
            "detail": "Direct video access blocked (CRIT-001). Use authenticated API endpoints.",
            "code": "VIDEO_ACCESS_BLOCKED",
            "help": "Access videos via: /api/videos/secure/{video_id}"
        }
    )

project_root = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=project_root / "static"), name="static")
mount_documentation(app, project_root)

# Registrasi routers
app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(cameras.router)
app.include_router(videos.router)
app.include_router(health.router)
app.include_router(config.router)
app.include_router(docs.router)
app.include_router(stats.router)
app.include_router(snapshots.router)
app.include_router(ping.router)
app.include_router(resolve_ip.router)
app.include_router(maps.router)
app.include_router(user_management.router)
app.include_router(user_profile.router)
app.include_router(logs.router)
app.include_router(nvrs.router)
app.include_router(snap_gallery.router)
app.include_router(audit.router)
app.include_router(audit_log.router)
app.include_router(dev_docs.router)
app.include_router(ws_router)
app.include_router(whatsapp_routes.router)
app.include_router(observability_log.router)
app.include_router(admin.router)
app.include_router(whitelist.router)
app.include_router(group_recipients.router)
app.include_router(email_logs.router)
app.include_router(email_templates.router)
app.include_router(wa_webhook.router)
app.include_router(insights.router)
app.include_router(notifications.router)
app.include_router(jobs.router)
app.include_router(camera_groups.router)
app.include_router(online_users.router)
app.include_router(record_checks.router)
# Demo routes - remove in production
app.include_router(toast_demo.router)

# ====================================================================
# 7. CORE APP ROUTES & HANDLERS
# ====================================================================
@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/maps")

def _wants_json_error(request: Request) -> bool:
    """Keep API and JSON-request errors in JSON, including status-code handlers."""
    return (
        "application/json" in request.headers.get("accept", "").lower()
        or request.url.path.startswith("/api/")
        or request.url.path.startswith("/snap/")
    )


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # Check if request expects JSON (API call)
    is_json_request = _wants_json_error(request)
    
    if exc.detail == "SESSION_INVALIDATED":
        # For API calls, return JSON error
        if is_json_request:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"status": "error", "detail": "Session invalidated", "code": "SESSION_INVALIDATED"}
            )
        # For web pages, redirect to login
        response = RedirectResponse(
            url="/login?reason=invalid_session",
            status_code=status.HTTP_303_SEE_OTHER
        )
        # FIX: Tambahkan path="/" agar cookie benar-benar terhapus
        response.delete_cookie("session", path="/")
        response.delete_cookie("session_token", path="/")
        # FIX: Hapus juga remember_me cookie
        from app.utils.remember_me import get_cookie_settings
        cookie_settings = get_cookie_settings()
        response.delete_cookie(cookie_settings["key"], path="/")
        return response

    if exc.status_code == status.HTTP_403_FORBIDDEN:
        if is_json_request:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"status": "error", "detail": exc.detail}
            )
        return templates.TemplateResponse(
            "unauthorized.html",
            {"request": request, "detail": exc.detail},
            status_code=exc.status_code
        )
    
    # Default: return JSON for API calls, plain text for others
    if is_json_request:
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "detail": exc.detail}
        )

    return Response(content=f"An error occurred: {exc.detail}", status_code=exc.status_code)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui-tailwind.css"
    )

@lru_cache()
def get_version_info():
    return {"version": __version__}

app.include_router(readiness_router)

@app.get("/version")
async def version():
    return get_version_info()

@app.get("/debug/ip")
async def debug_ip(request: Request):
    return {
        "client": request.client.host,
        "x-forwarded-for": request.headers.get("x-forwarded-for"),
        "x-real-ip": request.headers.get("x-real-ip")
    }

# FIX: Hapus route /documentation yang konflik dengan mount StaticFiles
# Gunakan index langsung: /documentation/index.html
# Jika ingin redirect, pakai path lain:
@app.get("/documentation-index", include_in_schema=False)
async def html_docs_redirect():
    return RedirectResponse(url="/documentation/index.html")

@app.exception_handler(404)
async def not_found_404(request: Request, exc):
    if _wants_json_error(request):
        return await custom_http_exception_handler(request, exc)
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.exception_handler(403)
async def forbidden_403(request: Request, exc):
    if _wants_json_error(request):
        return await custom_http_exception_handler(request, exc)
    return templates.TemplateResponse("403.html", {"request": request}, status_code=403)


import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    # Jika development, tampilkan detail
    if ENVIRONMENT == "development":
        return HTMLResponse(
            content=f"""
            <h1>INTERNAL SERVER ERROR - DEBUG MODE</h1>
            <h2>Error Type: {type(exc).__name__}</h2>
            <h2>Message: {str(exc)}</h2>
            <hr>
            <pre style="background: #f0f0f0; padding: 20px; overflow: auto;">{traceback.format_exc()}</pre>
            """,
            status_code=500
        )
    
    # Production: tampilkan pesan generic
    return HTMLResponse(
        content="<h1>Internal Server Error</h1><p>Please try again later.</p>",
        status_code=500
    )
