import logging
from fastapi import Depends, FastAPI, HTTPException, Response, status
from app.core.config_initializer import seed_config
from app.scheduler import start_scheduler
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from app.db.database import Base, engine, get_db
from contextlib import asynccontextmanager
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from app.routes import videos
from app.routes import cameras
from app.routes import health
from app.routes import config
from app.routes import docs
from app.routes import stats
from app.routes import snapshots
from app.routes import ping
from app.routes import resolve_ip
from app.routes import maps
from app.routes import auth
from app.routes import setup
from app.routes import user_management
from app.routes import logs
from app.routes import snap_gallery
from app.routes import audit
from app.routes import dev_docs
from app.db.database import SessionLocal
from app.models_sql import User
from app.core.logging_config import setup_logging
from app.routes import nvrs
from app.utils.audit_logger import log_audit
from sqlalchemy.orm import Session
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

# Setup logging
setup_logging()
logger = logging.getLogger("main")

# Save active websocket connections
websocket_connections = set()
logger.info("Starting main application")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed config default
    db = SessionLocal()
    try:
        seed_config(db)
        logger.info("Default configuration seeded.")
    finally:
        db.close()

    # Start background scheduler
    scheduler = start_scheduler()

    # App is running
    yield

    # On shutdown
    scheduler.shutdown(wait=False)

# app = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None)
app = FastAPI(
    lifespan=lifespan, title="B-Snap API",
    description="API documentation for B-Snap project",
    version="1.0.0",
    docs_url=None,  # default
    redoc_url=None  # optional)
)
# Mount static folder (for images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ====================================================================
# "OTAK" PENERJEMAH ERROR (EXCEPTION HANDLER)
# ====================================================================
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """
    Handler ini menangkap semua HTTPException dari aplikasi Anda.
    """
    # Jika status code adalah sinyal redirect kita (307)
    if exc.status_code == status.HTTP_307_TEMPORARY_REDIRECT:
        return RedirectResponse(url=exc.detail, status_code=exc.status_code)
    
    # Jika status code adalah 403 (Dilarang)
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        # Anda bisa meneruskan detail error dari 'raise' ke template
        return templates.TemplateResponse(
            "unauthorized.html", 
            {"request": request, "detail": exc.detail},
            status_code=exc.status_code
        )
    
    # Untuk error lain (seperti 404), Anda bisa membuat halaman error umum
    # atau biarkan default FastAPI yang menanganinya.
    return Response(
        content=f"Error: {exc.detail}", 
        status_code=exc.status_code
    )

ALLOWED_PATHS_DURING_SETUP = [
    "/mfa/force-setup",
    "/mfa/force-verify",
    "/logout",
    "/static",  # Allow access to CSS/JS files
]

class MFAEnforcementMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        # Allow access to a few specific paths
        if any(request.url.path.startswith(path) for path in ALLOWED_PATHS_DURING_SETUP):
            return await call_next(request)

        user_id = request.session.get("user_id")

        # If user is not logged in at all, do nothing.
        if not user_id:
            return await call_next(request)
        
        # We need a database session to check the user's status
        # This is a simple way to get a session within middleware
        db_session_generator = get_db()
        db: Session = next(db_session_generator)
        
        try:
            user = db.query(User).filter(User.id == user_id).first()
        finally:
            # Ensure the session is closed
            next(db_session_generator, None)
            
        # If user is logged in but has NOT enabled MFA, redirect them to the setup page.
        if user and not user.is_2fa_enabled:
            return RedirectResponse(url="/mfa/force-setup")

        # Otherwise, proceed to the requested page.
        response = await call_next(request)
        return response

# Add the middleware to your FastAPI app
app.add_middleware(MFAEnforcementMiddleware)

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
app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(user_management.router)
app.include_router(logs.router)
app.include_router(nvrs.router)
app.include_router(snap_gallery.router)
app.include_router(audit.router)
app.include_router(dev_docs.router)

app.add_middleware(SessionMiddleware, secret_key="secret123!@#")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Setup templates
templates = Jinja2Templates(directory="templates")


@app.middleware("http")
async def redirect_to_setup_if_no_users(request: Request, call_next):
    db = SessionLocal()
    has_user = db.query(User).first()
    db.close()

    path = request.url.path

    # Bypass static files and setup route
    if (
        has_user
        or path.startswith("/setup")
        or path.startswith("/static")
        # or path.startswith("/static/icons/favicon-white.ico")
    ):
        return await call_next(request)

    return RedirectResponse(url="/setup")


@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    log_audit(
        db=db,
        user=request.session.get("user_name", "unknown"),
        action="logout",
        target="",
        ip=request.client.host,
        extra=""
    )
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/")
def root_redirect(request: Request):
    print(f"Nilai user di sesi: {request.session.get('user_role')}") # Tambahkan baris ini
    if request.session.get("user_role"):
        return RedirectResponse(url="/maps")
    else:
        return RedirectResponse(url="/login")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception as e:
        # No log error if this is normal closure (1001)
        if not (isinstance(e, WebSocketDisconnect) and e.code == 1001):
            logger.info(f"WebSocket error: {str(e)}")
    finally:
        websocket_connections.remove(websocket)


from fastapi.openapi.docs import get_swagger_ui_html

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="B-Snap API Docs",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui-tailwind.css"  # yang kamu custom sendiri
    )
