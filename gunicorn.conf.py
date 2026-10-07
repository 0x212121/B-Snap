# gunicorn.conf.py  — FINAL
# -----------------------------------------------------------------------------
# Strategi:
# - Gunicorn pakai logconfig_dict => ia memanggil logging.config.dictConfig()
#   dengan LOGGING_CONFIG kamu. Jadi ga ada tarik-menarik handler.
# - Kita re-apply dictConfig lagi di post_fork (aman), supaya setiap worker
#   punya handler file masing-masing (multi-process friendly).
# - TIDAK set accesslog/errorlog ke "-" atau "" (biar gak override/ crash).
# - Tetap ada console handler di LOGGING_CONFIG kamu, jadi journalctl/docker logs jalan.

import os, sys
from pathlib import Path
from logging.config import dictConfig

# Pastikan root project ke sys.path agar 'app.core.logging_config' bisa di-import
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.logging_config import LOGGING_CONFIG, set_debug_mode

# --- Worker & binding
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "2")))
bind = os.getenv("BIND", "0.0.0.0:8080")
timeout = int(os.getenv("TIMEOUT", "60"))
graceful_timeout = int(os.getenv("GRACEFUL_TIMEOUT", "90"))
keepalive = int(os.getenv("KEEPALIVE", "2"))

# --- Logging: pakai dictConfig milik aplikasi (yang sudah include gunicorn/uvicorn logger)
logconfig_dict = LOGGING_CONFIG

# Jangan set accesslog/errorlog di sini.
# Biarkan logconfig_dict yang atur semua logger (gunicorn.error, gunicorn.access, uvicorn.*)
# Kalau kamu PAKSA taruh "-" di sini, Gunicorn akan override handler lagi.
# accesslog = "-"
# errorlog  = "-"

# Level untuk logger internal Gunicorn (nggak mengubah logger aplikasi kamu).
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")

# Tangkap stdout/stderr ke logging Python (console handler kamu tetap aktif)
capture_output = True

def post_fork(server, worker):
    # Re-apply dictConfig di setiap worker (aman untuk multi-process)
    dictConfig(LOGGING_CONFIG)

    # Coba aktifkan debug mode dari database atau environment variable
    debug_enabled = False
    
    # 1. Cek environment variable dulu
    if os.getenv("APP_DEBUG", "0").lower() in ("1", "true", "yes"):
        debug_enabled = True
    
    # 2. Coba baca dari database jika belum enabled via ENV
    if not debug_enabled:
        try:
            # Delay import agar tidak crash saat startup
            import sys
            from pathlib import Path
            ROOT = Path(__file__).resolve().parent
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from app.models.config import Configuration
            import app.db.database as db_module
            
            # Gunakan database URL dari environment atau default
            db_url = os.getenv("DATABASE_URL", "sqlite:///./data.db")
            engine = create_engine(db_url, pool_pre_ping=True)
            Session = sessionmaker(bind=engine)
            session = Session()
            
            try:
                debug_config = session.query(Configuration).filter_by(key="debug_mode").first()
                if debug_config and debug_config.value == "1":
                    debug_enabled = True
            finally:
                session.close()
                engine.dispose()
                
        except Exception:
            # Jika gagal baca dari database, abaikan saja
            pass
    
    # Terapkan debug mode jika diaktifkan
    if debug_enabled:
        set_debug_mode(True)
        server.log.info("[Worker %s] Debug mode ENABLED", worker.pid)
    
    server.log.info("[Worker %s] Logging initialized via logconfig_dict", worker.pid)
