# app/core/logging_config.py
import logging
import logging.config
from pathlib import Path
import os
from datetime import datetime, timezone

# pip install concurrent-log-handler
from concurrent_log_handler import ConcurrentRotatingFileHandler  # type: ignore

# === Path absolut untuk folder log ===
BASE_DIR = Path(__file__).resolve().parents[2]  # sesuaikan jika struktur berbeda
LOG_DIR = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# === Global state untuk debug mode ===
# Variabel ini menyimpan state debug mode secara global agar bisa diterapkan
# setiap kali setup_logging() dipanggil (misalnya di worker baru)
_debug_mode_enabled: bool = False

def _get_debug_mode() -> bool:
    """Get current debug mode state."""
    global _debug_mode_enabled
    return _debug_mode_enabled

def _set_debug_mode_state(enabled: bool):
    """Set debug mode state (internal use)."""
    global _debug_mode_enabled
    _debug_mode_enabled = enabled


# === UTC Formatter Class ===
class UTCFormatter(logging.Formatter):
    """
    Formatter that always uses UTC time for log timestamps.
    This ensures consistency with database timestamps (which are all UTC).
    Uses ISO 8601 format with timezone offset.
    """
    converter = lambda *args: datetime.now(timezone.utc).timetuple()
    
    def formatTime(self, record, datefmt=None):
        """Format timestamp in UTC using ISO 8601 format."""
        dt = datetime.fromtimestamp(record.created, timezone.utc)
        # ISO 8601 format: 2026-03-30T06:47:13+00:00
        return dt.isoformat(timespec='seconds')

# === Path file log ===
MAIN_LOG = LOG_DIR / "main.log"
SNAPSHOT_LOG = LOG_DIR / "snapshot.log"
HEALTH_LOG = LOG_DIR / "healthcheck.log"
SCHED_LOG = LOG_DIR / "scheduler.log"
MGMT_LOG = LOG_DIR / "management.log"

# === Utility handler builder ===
# Default: 10MB per file, max 10 backup files = max 110MB per category
def _file_handler(filename: Path, formatter: str, level: str = "INFO", max_bytes=10_000_000, backups=10):
    return {
        "class": "concurrent_log_handler.ConcurrentRotatingFileHandler",
        "filename": str(filename),
        "formatter": formatter,
        "level": level,
        "maxBytes": max_bytes,
        "backupCount": backups,
        "encoding": "utf-8",
    }

# === Konfigurasi utama logging ===
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,  # penting agar logger Gunicorn/Uvicorn tidak dibungkam

    "formatters": {
        "standard": {
            "()": UTCFormatter,
            "format": "[%(asctime)s] [%(levelname)s] [%(name)s] [pid=%(process)d] %(message)s"
        },
        "access": {
            "()": UTCFormatter,
            "format": "[%(asctime)s] [%(levelname)s] [%(name)s] [pid=%(process)d] %(message)s"
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "DEBUG",
        },
        "main_file": _file_handler(MAIN_LOG, "standard", level="INFO"),
        "snapshot_file": _file_handler(SNAPSHOT_LOG, "standard", level="INFO"),
        "healthcheck_file": _file_handler(HEALTH_LOG, "standard", level="INFO"),
        "scheduler_file": _file_handler(SCHED_LOG, "standard", level="INFO"),
        "management_file": _file_handler(MGMT_LOG, "standard", level="INFO"),
        "access_file": _file_handler(MAIN_LOG, "access", level="INFO"),
    },

    "loggers": {
        # === Logger aplikasi utama ===
        "main": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },

        # === Snapshot dan semua turunannya (deteksi & email) ===
        "snapshot": {
            "handlers": ["snapshot_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "email_notifier": {
            "handlers": ["snapshot_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "email_helper": {
            "handlers": ["snapshot_file", "console"],
            "level": "INFO",
            "propagate": False,
        },

        # === Third-party libraries ===
        # Zeep (SOAP/ONVIF) - log ke main file agar debug ONVIF tersimpan
        "zeep": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "zeep.transports": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },

        # === Modul lainnya ===
        "healthcheck": {
            "handlers": ["healthcheck_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "scheduler": {
            "handlers": ["scheduler_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "management": {
            "handlers": ["management_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "storage_monitor": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "storage": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "websocket": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "auth": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "ping": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "app.insights": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False,
        },

        # # === SQLAlchemy & Server ===
        # "sqlalchemy.engine": {"handlers": ["main_file", "console"], "level": "INFO", "propagate": False},
        # "sqlalchemy.pool":   {"handlers": ["main_file", "console"], "level": "WARN", "propagate": False},
        # "sqlalchemy.orm":    {"handlers": ["main_file", "console"], "level": "WARN", "propagate": False},

        "gunicorn.error":  {"handlers": ["main_file", "console"],  "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": ["access_file", "console"],"level": "INFO", "propagate": False},
        "uvicorn.error":   {"handlers": ["main_file", "console"],  "level": "INFO", "propagate": False},
        "uvicorn.access":  {"handlers": ["access_file", "console"],"level": "INFO", "propagate": False},
    },

    # Root fallback → menangkap semua logger lain
    "root": {
        "handlers": ["console", "main_file"],
        "level": "INFO",
    },
}


# === Fungsi utilitas ===
def setup_logging():
    """
    Inisialisasi logging dengan konfigurasi di atas.
    Aman dipanggil ulang di setiap proses (Gunicorn worker, scheduler, dsb).
    
    Jika debug mode pernah diaktifkan via set_debug_mode(), level akan
    otomatis diatur ke DEBUG untuk semua logger dan handler.
    """
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.config.dictConfig(LOGGING_CONFIG)
    
    # Terapkan debug mode jika pernah diaktifkan sebelumnya
    # Ini penting untuk worker baru atau proses yang baru di-spawn
    if _get_debug_mode():
        _apply_debug_mode_to_all_loggers(True)
    else:
        # Pastikan tidak ada handler DEBUG yang tertinggal dari proses lain
        # atau dari library eksternal yang mungkin sudah menambahkan handler
        _apply_debug_mode_to_all_loggers(False)


def _apply_debug_mode_to_all_loggers(enabled: bool):
    """
    Internal function to apply debug level to all existing loggers and handlers.
    """
    new_level = logging.DEBUG if enabled else logging.INFO
    
    # root
    root = logging.getLogger()
    root.setLevel(new_level)
    for h in root.handlers:
        h.setLevel(new_level)
    
    # semua logger bernama dari config
    for name in LOGGING_CONFIG["loggers"].keys():
        lg = logging.getLogger(name)
        lg.setLevel(new_level)
        for h in lg.handlers:
            h.setLevel(new_level)
    
    # juga tangani logger yang mungkin sudah dibuat tapi tidak di config
    # (misalnya logger dari third-party libraries)
    # Tapi skip logger sqlalchemy untuk menghindari log noise
    skip_prefixes = ("sqlalchemy", "alembic")
    for name in logging.root.manager.loggerDict.keys():
        if name not in LOGGING_CONFIG["loggers"] and not name.startswith(skip_prefixes):
            lg = logging.getLogger(name)
            # Hanya ubah level, jangan tambahkan handler baru
            lg.setLevel(new_level)
            # Tapi handler-nya tetap perlu diupdate (penting untuk uvicorn/gunicorn)
            for h in lg.handlers:
                h.setLevel(new_level)
    
    # PENTING: Pastikan semua StreamHandler (console) di seluruh logging system
    # memiliki level yang konsisten. Ini menangani handler yang mungkin
    # ditambahkan oleh uvicorn/gunicorn secara dinamis.
    _update_all_console_handlers(new_level)


def _update_all_console_handlers(level: int):
    """
    Update level untuk semua console handler (StreamHandler) di seluruh system.
    Fungsi ini memastikan tidak ada console handler yang tertinggal dengan level DEBUG
    ketika debug mode dimatikan.
    """
    # Update StreamHandler di root
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(level)
    
    # Update StreamHandler di semua logger yang terdaftar
    for name in list(logging.root.manager.loggerDict.keys()):
        try:
            lg = logging.getLogger(name)
            for h in lg.handlers:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    h.setLevel(level)
        except Exception:
            # Abaikan logger yang mungkin sudah tidak valid
            pass


def sanitize_loggers():
    """
    Fungsi untuk memastikan semua logger dan handler mengikuti global debug mode state.
    Fungsi ini bisa dipanggil secara periodik atau saat menerima request untuk
    menangani handler yang mungkin ditambahkan oleh library eksternal (uvicorn, etc)
    setelah set_debug_mode() terakhir dipanggil.
    """
    if not _get_debug_mode():
        # Jika debug mode tidak aktif, pastikan tidak ada handler DEBUG
        _apply_debug_mode_to_all_loggers(False)


def set_debug_mode(enabled: bool):
    """
    Aktif/nonaktifkan debug mode secara dinamis.
    
    State akan disimpan secara global sehingga setup_logging() yang dipanggil
    di worker/proses baru akan otomatis menerapkan debug mode.
    """
    # Simpan state ke variabel global
    _set_debug_mode_state(enabled)
    
    # Terapkan ke semua logger yang sudah ada
    _apply_debug_mode_to_all_loggers(enabled)
    
    app_logger = logging.getLogger("main")
    if enabled:
        app_logger.debug("✅ Debug mode ENABLED - All loggers set to DEBUG level")
    else:
        app_logger.info("ℹ️ Debug mode DISABLED - All loggers set to INFO level")
