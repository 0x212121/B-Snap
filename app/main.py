# ====================================================================
# 1. IMPORTS
# ====================================================================
import asyncio
import logging
import os
from contextlib import asynccontextmanager
import traceback

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import ORJSONResponse, RedirectResponse
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

from app.core.config_initializer import seed_config
from app.core.logging_config import setup_logging
from app.db.database import Base, engine, SessionLocal

# Import all models to register them with Base.metadata
# This MUST happen before Base.metadata.create_all() is called
import app.models  # noqa: F401 - imports all models via __init__.py
from app.routes import (
    admin, auth, audit, cameras, config, dev_docs, docs, health, jobs, logs, maps,
    nvrs, ping, resolve_ip, setup, snap_gallery, snapshots, stats,
    user_management, videos, whitelist, group_recipients, email_logs, wa_webhook,
    notifications, toast_demo
)
from app.routes import insights
from app.ws.routes import notification_listener, router as ws_router
from app.version import __version__
from app.api import whatsapp_routes
from app.api import observability_log
from app.ws.manager import websocket_connections
from functools import lru_cache
from alembic.config import Config
from alembic import command
from pathlib import Path

from app.utils.template_helper import templates

# Get project root for alembic config
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI_PATH = os.getenv("ALEMBIC_INI_PATH", str(PROJECT_ROOT / "alembic.ini"))

# ====================================================================
# 2. INITIAL SETUP & CONFIGURATION
# ====================================================================
if os.getenv("BSNAP_LOG_VIA_GUNICORN", "1") not in ("1", "true", "yes"):
    setup_logging()

logger = logging.getLogger("main")

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

    # Always create missing tables (for new models that don't have migrations yet)
    logger.info("Creating any missing tables from Base metadata...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Table creation check complete.")
    except Exception as e:
        logger.error(f"Failed to create tables: {e}")
    
    if not tables:
        logger.info("No existing tables found, stamping Alembic version...")
        try:
            alembic_cfg = Config(ALEMBIC_INI_PATH)
            command.stamp(alembic_cfg, "head")
            logger.info("Alembic schema version stamped to head.")
        except Exception as e:
            logger.error(f"Failed to stamp Alembic version: {e}")
    else:
        logger.info("Tables already exist, running Alembic upgrade...")
        try:
            alembic_cfg = Config(ALEMBIC_INI_PATH)
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied.")
        except Exception as e:
            logger.error(f"Alembic migration failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Don't raise - allow app to start even if migration fails
            # This prevents worker boot failure due to migration issues

    db = SessionLocal()
    try:
        seed_config(db)
        logger.info("Database seeded with initial configuration.")
    except Exception as e:
        logger.error(f"Database seeding failed: {e}")
        # Don't raise - allow app to start
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
        yield
    finally:
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
    Middleware(RealIPFixMiddleware),   # ✅ Tambahkan ini
    Middleware(GZipMiddleware, minimum_size=1000),
    Middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=3600),
    Middleware(RestoreSessionMiddleware),
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
            return templates.TemplateResponse("500.html", {"request": request}, status_code=500)
        return response
    except Exception as exc:
        error_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(f"Unhandled error at {request.url}:\n{error_trace}")
        return templates.TemplateResponse("500.html", {"request": request}, status_code=500)

# ====================================================================
# 6. ROUTERS & STATIC FILES
# ====================================================================

# P2-004: Custom StaticFiles that blocks direct snapshot access
class SecureStaticFiles(StaticFiles):
    """StaticFiles that blocks access to /static/snapshots (P2-004).
    
    Snapshots must be accessed via authenticated API endpoints:
    - GET /api/snapshots/secure/{snapshot_id}
    - GET /api/snapshots/file/{file_path}
    """
    
    async def get_response(self, path: str, scope):
        # Block direct access to snapshots
        if path.startswith("snapshots/"):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={
                    "status": "error",
                    "detail": "Direct snapshot access blocked (P2-004). Use authenticated API endpoints.",
                    "code": "SNAPSHOT_ACCESS_BLOCKED"
                }
            )
        return await super().get_response(path, scope)

app.mount("/static", SecureStaticFiles(directory="static"), name="static")
app.mount("/documentation", StaticFiles(directory="docs/build/html"), name="docs")

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
app.include_router(logs.router)
app.include_router(nvrs.router)
app.include_router(snap_gallery.router)
app.include_router(audit.router)
app.include_router(dev_docs.router)
app.include_router(ws_router)
app.include_router(whatsapp_routes.router)
app.include_router(observability_log.router)
app.include_router(admin.router)
app.include_router(whitelist.router)
app.include_router(group_recipients.router)
app.include_router(email_logs.router)
app.include_router(wa_webhook.router)
app.include_router(insights.router)
app.include_router(notifications.router)
app.include_router(jobs.router)
# Demo routes - remove in production
app.include_router(toast_demo.router)

# ====================================================================
# 7. CORE APP ROUTES & HANDLERS
# ====================================================================
@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/maps")

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # Check if request expects JSON (API call)
    accept_header = request.headers.get("accept", "")
    is_json_request = "application/json" in accept_header or request.url.path.startswith("/api/") or request.url.path.startswith("/snap/")
    
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
        response.delete_cookie("session")
        response.delete_cookie("session_token")
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
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.exception_handler(403)
async def forbidden_403(request: Request, exc):
    return templates.TemplateResponse("403.html", {"request": request}, status_code=403)
