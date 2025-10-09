# app/middleware/observability.py
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models.log import ApiLog

EXCLUDE_PATHS = [
    "/static",
    "/favicon.ico",
    "/health",
    "/ws"
]

class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Abaikan static dan websocket
        if any(request.url.path.startswith(p) for p in EXCLUDE_PATHS):
            return await call_next(request)

        start_time = time.perf_counter()
        response = None
        error_message = None

        try:
            response = await call_next(request)
            return response
        except Exception as e:
            # Tangkap error supaya bisa dicatat
            error_message = str(e)
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            try:
                with SessionLocal() as db:
                    log = ApiLog(
                        user_id=request.session.get("user_name", "anonymous"),
                        endpoint=request.url.path,
                        method=request.method,
                        status_code=response.status_code if response else 500,
                        source="web",
                        user_agent=request.headers.get("user-agent"),
                        timestamp=None,  # default func.now()
                    )
                    # simpan juga durasi dan pesan error kalau mau tambahkan di model
                    if hasattr(ApiLog, "duration_ms"):
                        log.duration_ms = duration_ms
                    if hasattr(ApiLog, "error_message") and error_message:
                        log.error_message = error_message
                    if hasattr(ApiLog, "ip_address"):
                        log.ip_address = request.client.host
                    db.add(log)
                    db.commit()
            except Exception as e:
                # Jangan sampai logging gagal bikin request error
                print(f"[ObservabilityMiddleware] Failed to log API: {e}")
