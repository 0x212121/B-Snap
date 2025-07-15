# ====================================================================
# 1. IMPORTS
# ====================================================================
# Python Standard Library
import logging
import os
from contextlib import asynccontextmanager
import datetime

# Third-party Libraries
from fastapi import (
    Depends, FastAPI, HTTPException, Request, Response, status,
    WebSocket, WebSocketDisconnect
)
from app.ws_manager import websocket_connections
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# Application-specific Imports
from app.core.config_initializer import seed_config
from app.core.logging_config import setup_logging
from app.db.database import Base, engine, SessionLocal
from app.models_sql import User
from app.jobs.scheduler import start_scheduler
# Combined router imports for cleaner organization
from app.routes import (
    auth, audit, cameras, config, dev_docs, docs, health, logs, maps,
    nvrs, ping, resolve_ip, setup, snap_gallery, snapshots, stats,
    user_management, videos
)
from app.routes.auth import get_current_user

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

templates = Jinja2Templates(directory="templates")

# List of paths that can be accessed without login
ALLOWED_PUBLIC_PATHS = [
    "/login",
    "/logout",
    "/setup",
    "/static/css",
    "/static/icons",
    "/static/swagger-ui-tailwind.css",
    "/docs",
    "/openapi.json",
    "/mfa/setup",
    "/mfa/force-verify"
]

# ====================================================================
# 3. APPLICATION LIFESPAN (STARTUP & SHUTDOWN)
# ====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles events when the application starts and stops."""
    logger.info("application is starting up...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        seed_config(db)
        logger.info("Database seeded with initial configuration.")
    finally:
        db.close()

    yield  # --- Application is running ---

    logger.info("Application is shutting down...")

# ====================================================================
# 4. FASTAPI APP INSTANCE & MIDDLEWARE
# ====================================================================
app = FastAPI(
    lifespan=lifespan,
    title="B-Snap API",
    description="B-Snap Documentation API",
    version="1.0.0",
    docs_url=None,  # Disabled to use custom docs
    redoc_url=None
)

# --- Main Middleware (Combined) ---
class AuthAndSetupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        db = SessionLocal()
        try:
            current_path = request.url.path
            auth_header = request.headers.get("authorization")
            token_query = request.query_params.get("token")  # ✅ ambil token dari query param

            # Step 1: Setup check
            if not db.query(User).first():
                if not any(current_path.startswith(p) for p in ["/setup", "/static"]):
                    return RedirectResponse(url="/setup", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
                return await call_next(request)

            # Step 2: Allow public paths
            if any(current_path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)

            # ✅ Step 2.5: Allow access if signed token is provided (e.g. /snapshot/file/... or /snapshot/token/...)
            if token_query:
                return await call_next(request)

            # Step 3: If Bearer token is present, skip session check
            if auth_header and auth_header.lower().startswith("bearer "):
                return await call_next(request)

            # Step 4: Session-based auth fallback
            user_id = request.session.get("user_id")
            if not user_id:
                return RedirectResponse(
                    url="/login",
                    status_code=status.HTTP_303_SEE_OTHER
                )

            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                request.session.clear()
                response = RedirectResponse(
                    url="/login?reason=invalid_session",
                    status_code=status.HTTP_303_SEE_OTHER
                )
                response.delete_cookie("session_token")
                return response

            if not user.is_2fa_enabled:
                return RedirectResponse(url="/mfa/setup", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

            return await call_next(request)

        except Exception as e:
            logger.error(f"Error in AuthAndSetupMiddleware: {e}", exc_info=True)
            return Response("Internal Server Error", status_code=500)
        finally:
            db.close()

# Middleware order is IMPORTANT: Processed from bottom to top when request comes in.
# Last added will be executed first.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_HOSTS)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(AuthAndSetupMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=3600)


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


# ====================================================================
# 7. CORE APP ROUTES & HANDLERS
# ====================================================================
@app.get("/", include_in_schema=False)
def root_redirect(user: User = Depends(get_current_user)):
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        websocket_connections.remove(websocket)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui-tailwind.css"
    )
