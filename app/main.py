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
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.gzip import GZipMiddleware

# Application-specific Imports
from app.core.config_initializer import seed_config
from app.core.logging_config import setup_logging
from app.db.database import Base, engine, SessionLocal
from app.models_sql import User
from app.scheduler import start_scheduler
# Impor router disatukan agar lebih rapi
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

# Pastikan SECRET_KEY ada, jika tidak gunakan nilai default yang aman untuk pengembangan
SECRET_KEY = os.getenv("SECRET_KEY", "your-default-secret-key-for-dev")
if SECRET_KEY == "your-default-secret-key-for-dev":
    logger.warning("Using default SECRET_KEY. This is not secure for production.")

templates = Jinja2Templates(directory="templates")
websocket_connections = set()

# Daftar path yang boleh diakses tanpa login.
ALLOWED_PUBLIC_PATHS = [
    "/login",
    "/logout",
    "/setup",
    "/static",
    "/docs",
    "/openapi.json",
    "/mfa/setup",
    "/mfa/force-verify",
]

# ====================================================================
# 3. APPLICATION LIFESPAN (STARTUP & SHUTDOWN)
# ====================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Menangani event saat aplikasi mulai dan berhenti."""
    logger.info("Aplikasi mulai berjalan...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        seed_config(db)
        logger.info("Konfigurasi default telah di-seed.")
    finally:
        db.close()

    scheduler = start_scheduler()
    logger.info("Scheduler latar belakang dimulai.")

    yield  # --- Aplikasi sedang berjalan ---

    logger.info("Aplikasi mulai berhenti...")
    scheduler.shutdown(wait=False)
    logger.info("Scheduler latar belakang berhenti.")

# ====================================================================
# 4. FASTAPI APP INSTANCE & MIDDLEWARE
# ====================================================================
app = FastAPI(
    lifespan=lifespan,
    title="B-Snap API",
    description="Dokumentasi API untuk proyek B-Snap",
    version="1.0.0",
    docs_url=None,  # Dinonaktifkan untuk menggunakan docs kustom
    redoc_url=None,
)

# --- Middleware Utama (Digabung) ---
class AuthAndSetupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        db = SessionLocal()
        try:
            current_path = request.url.path

            # Langkah 1 & 2: Cek Setup Awal dan Path Publik
            if not db.query(User).first():
                if not any(current_path.startswith(p) for p in ["/setup", "/static"]):
                    return RedirectResponse(url="/setup", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
                return await call_next(request)

            if any(current_path.startswith(p) for p in ALLOWED_PUBLIC_PATHS):
                return await call_next(request)

            # Logika ini sekarang menjadi satu-satunya sumber kebenaran untuk sesi yang tidak valid.
            user_id = request.session.get("user_id")
            
            # KASUS 1: Tidak ada user_id di sesi (cookie tidak ada atau expired)
            if not user_id:
                return RedirectResponse(
                    url="/login", 
                    status_code=status.HTTP_303_SEE_OTHER
                )

            # KASUS 2: Ada user_id, tapi user tidak ditemukan di DB (sesi basi/stale)
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                request.session.clear() # Bersihkan sesi yang tidak valid
                response = RedirectResponse(
                    url="/login?reason=invalid_session", 
                    status_code=status.HTTP_303_SEE_OTHER
                )
                response.delete_cookie("session_token") # Hapus juga cookie lama
                return response
            
            # Langkah 4: Cek MFA
            if not user.is_2fa_enabled:
                return RedirectResponse(url="/mfa/setup", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

            # Jika semua valid, lanjutkan ke endpoint yang dituju.
            return await call_next(request)
        
        except Exception as e:
            logger.error(f"Error in AuthAndSetupMiddleware: {e}", exc_info=True)
            return Response("Internal Server Error", status_code=500)
        finally:
            db.close()

# Urutan Middleware PENTING: Diproses dari bawah ke atas saat request masuk.
# Yang ditambahkan terakhir, akan dieksekusi pertama.
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(AuthAndSetupMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


# ====================================================================
# 6. ROUTERS & STATIC FILES
# ====================================================================
app.mount("/static", StaticFiles(directory="static"), name="static")

# Mengelompokkan router agar lebih terorganisir
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
    # BARU: Tangani sinyal "SESSION_INVALIDATED" secara khusus untuk redirect
    if exc.detail == "SESSION_INVALIDATED":
        # Buat respons redirect ke halaman login
        response = RedirectResponse(
            url="/login?reason=invalid_session",
            status_code=status.HTTP_303_SEE_OTHER
        )
        # Hapus cookie sesi yang mungkin masih tersisa di browser
        response.delete_cookie("session")
        response.delete_cookie("session_token")
        return response

    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return templates.TemplateResponse(
            "unauthorized.html",
            {"request": request, "detail": exc.detail},
            status_code=exc.status_code
        )
    # Untuk semua error lainnya, tampilkan pesan default
    return Response(
        content=f"Terjadi error: {exc.detail}",
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
