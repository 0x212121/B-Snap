# ====================================================================
# 1. IMPORTS
# ====================================================================
# Python Standard Library
import asyncio
import logging
import os
from contextlib import asynccontextmanager

# Third-party Libraries
from fastapi import (
    FastAPI, HTTPException, Request, Response, status
)
from app.api import whatsapp_routes
from app.ws.manager import websocket_connections
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from app.middleware.auth_and_setup import AuthAndSetupMiddleware
from starlette.middleware.sessions import SessionMiddleware
from app.middleware.session_restore import RestoreSessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# Application-specific Imports
from app.core.config_initializer import seed_config
from app.core.logging_config import setup_logging
from app.db.database import Base, engine, SessionLocal
# Combined router imports for cleaner organization
from app.routes import (
    admin, auth, audit, cameras, config, dev_docs, docs, health, logs, maps,
    nvrs, ping, resolve_ip, setup, snap_gallery, snapshots, stats,
    user_management, videos, whitelist
)
from app.ws.routes import notification_listener, router as ws_router

# from app.ws.notifier import pg_listen_and_broadcast

# ====================================================================
# 2. INITIAL SETUP & CONFIGURATION
# ====================================================================
setup_logging()
logger = logging.getLogger("main")

# Ensure SECRET_KEY exists, if not use a safe default value for development
SECRET_KEY = os.getenv("SECRET_KEY", "your-default-secret-key-for-dev")
if SECRET_KEY == "your-default-secret-key-for-dev":
    logger.warning("Using default SECRET_KEY. This is not secure for production.")

TRUSTED_HOSTS = os.getenv("TRUSTED_HOSTS", "*")

from app.utils.template_helper import templates

# B-snap version
from app.version import __version__
templates.env.globals["version"] = __version__

# ====================================================================
# 3. APPLICATION LIFESPAN (STARTUP & SHUTDOWN)
# ====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    # Setup database
    logger.info("Lifespan startup: Initializing database...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        seed_config(db)
        logger.info("Database seeded with initial configuration.")
    finally:
        db.close()

    # Run listener notification
    logger.info("Lifespan startup: Creating persistent notification listener task...")
    task = asyncio.create_task(notification_listener(websocket_connections))
    # Save in state to prevent task destroy by garbage collector
    app.state.notification_listener_task = task
    
    yield  # Application run after this
    
    # --- SHUTDOWN ---
    # Stop listener gracefully
    logger.info("Lifespan shutdown: Cleaning up notification listener task...")
    app.state.notification_listener_task.cancel()
    try:
        await app.state.notification_listener_task
    except asyncio.CancelledError:
        logger.info("Notification listener task successfully cancelled.")
    
    logger.info("Application is shutting down...")

# ====================================================================
# 4. FASTAPI APP INSTANCE & MIDDLEWARE
# ====================================================================
from starlette.middleware import Middleware

middleware = [
    Middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_HOSTS),
    Middleware(GZipMiddleware, minimum_size=1000),
    Middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=3600),
    Middleware(RestoreSessionMiddleware),
    Middleware(AuthAndSetupMiddleware),
]

app = FastAPI(
    lifespan=lifespan,
    title="B-Snap API",
    description="B-Snap Documentation API",
    version="1.2.1",
    docs_url=None,  # Disabled to use custom docs
    redoc_url=None,
    middleware=middleware
)


# ====================================================================
# 6. ROUTERS & STATIC FILES
# ====================================================================
app.mount("/static", StaticFiles(directory="static"), name="static")

# Organize routers for better organization
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
app.include_router(admin.router)
app.include_router(whitelist.router)

# ====================================================================
# 7. CORE APP ROUTES & HANDLERS
# ====================================================================
@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/maps")


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # NEW: Handle "SESSION_INVALIDATED" signal specifically for redirect
    if exc.detail == "SESSION_INVALIDATED":
        # Create redirect response to login page
        response = RedirectResponse(
            url="/login?reason=invalid_session",
            status_code=status.HTTP_303_SEE_OTHER
        )
        # Delete any remaining session cookies in the browser
        response.delete_cookie("session")
        response.delete_cookie("session_token")
        return response

    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return templates.TemplateResponse(
            "unauthorized.html",
            {"request": request, "detail": exc.detail},
            status_code=exc.status_code
        )
    # For all other errors, display default message
    return Response(
        content=f"An error occurred: {exc.detail}",
        status_code=exc.status_code
    )


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui-tailwind.css"
    )


@app.get("/version")
async def get_version():
    from app.version import __version__
    return {"version": __version__}