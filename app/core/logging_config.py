# app/core/logging_config.py
import logging
import logging.config
from pathlib import Path
import os

# pip install concurrent-log-handler
from concurrent_log_handler import ConcurrentRotatingFileHandler  # type: ignore

# === Path absolut untuk folder log ===
BASE_DIR = Path(__file__).resolve().parents[2]  # sesuaikan jika struktur berbeda
LOG_DIR = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# === Path file log ===
MAIN_LOG = LOG_DIR / "main.log"
SNAPSHOT_LOG = LOG_DIR / "snapshot.log"
HEALTH_LOG = LOG_DIR / "healthcheck.log"
SCHED_LOG = LOG_DIR / "scheduler.log"
MGMT_LOG = LOG_DIR / "management.log"

# === Utility handler builder ===
def _file_handler(filename: Path, formatter: str, level: str = "INFO", max_bytes=5_000_000, backups=10):
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
            "format": "[%(asctime)s] [%(levelname)s] [%(name)s] [pid=%(process)d] %(message)s"
        },
        "access": {
            "format": '%(asctime)s [%(levelname)s] [%(name)s] [pid=%(process)d] %(client_addr)s - "%(request_line)s" %(status_code)s'
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "DEBUG",
        },
        "main_file": _file_handler(MAIN_LOG, "standard", level="INFO", max_bytes=10_000_000, backups=10),
        "snapshot_file": _file_handler(SNAPSHOT_LOG, "standard", level="INFO"),
        "healthcheck_file": _file_handler(HEALTH_LOG, "standard", level="INFO"),
        "scheduler_file": _file_handler(SCHED_LOG, "standard", level="INFO"),
        "management_file": _file_handler(MGMT_LOG, "standard", level="INFO"),
        "access_file": _file_handler(MAIN_LOG, "access", level="INFO", max_bytes=10_000_000, backups=10),
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
    """
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.config.dictConfig(LOGGING_CONFIG)


def set_debug_mode(enabled: bool):
    """
    Aktif/nonaktifkan debug mode secara dinamis.
    """
    new_level = logging.DEBUG if enabled else logging.INFO

    # root
    root = logging.getLogger()
    root.setLevel(new_level)
    for h in root.handlers:
        h.setLevel(new_level)

    # semua logger bernama
    for name in LOGGING_CONFIG["loggers"].keys():
        lg = logging.getLogger(name)
        lg.setLevel(new_level)
        for h in lg.handlers:
            h.setLevel(new_level)

    app_logger = logging.getLogger("main")
    if enabled:
        app_logger.debug("✅ Debug mode ENABLED")
    else:
        app_logger.info("ℹ️ Debug mode DISABLED")
