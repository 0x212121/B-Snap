import logging
import logging.config
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "standard": {
            "format": "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
        }
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "DEBUG"
        },
        "main_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "main.log"),
            "formatter": "standard",
            "level": "INFO",
            "maxBytes": 1_000_000,
            "backupCount": 5,
            "encoding": "utf-8"
        },
        "snapshot_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "snapshot.log"),
            "formatter": "standard",
            "level": "INFO",
            "maxBytes": 5_000_000,
            "backupCount": 5,
            "encoding": "utf-8"
        },
        "healthcheck_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "healthcheck.log"),
            "formatter": "standard",
            "level": "INFO",
            "maxBytes": 1_000_000,
            "backupCount": 5,
            "encoding": "utf-8"
        },
        "scheduler_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "scheduler.log"),
            "formatter": "standard",
            "level": "INFO",
            "maxBytes": 1_000_000,
            "backupCount": 5,
            "encoding": "utf-8"
        },
        "management_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(LOG_DIR, "management.log"),
            "formatter": "standard",
            "level": "INFO",
            "maxBytes": 1_000_000,
            "backupCount": 5,
            "encoding": "utf-8"
        }
    },

    "loggers": {
        "main": {
            "handlers": ["main_file", "console"],
            "level": "INFO",
            "propagate": False
        },
        "snapshot": {
            "handlers": ["snapshot_file"],
            "level": "INFO",
            "propagate": False
        },
        "healthcheck": {
            "handlers": ["healthcheck_file", "console"],
            "level": "INFO",
            "propagate": False
        },
        "scheduler": {
            "handlers": ["scheduler_file"],
            "level": "INFO",
            "propagate": False
        },
        "management": {
            "handlers": ["management_file"],
            "level": "INFO",
            "propagate": False
        },
    }
}


def setup_logging():
    logging.shutdown()
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.config.dictConfig(LOGGING_CONFIG)


def set_debug_mode(enabled: bool):
    """
    Mengubah level log seluruh logger & handler sesuai status debug mode.
    """
    new_level = "DEBUG" if enabled else "INFO"

    # Ubah level di loggers yang sudah terdaftar
    for logger_name, logger_conf in LOGGING_CONFIG["loggers"].items():
        logger_conf["level"] = new_level
        logger = logging.getLogger(logger_name)
        logger.setLevel(new_level)
        # Ubah level di semua handler logger ini
        for h in logger.handlers:
            h.setLevel(new_level)

    # Ubah level di root handlers juga
    for h in logging.getLogger().handlers:
        h.setLevel(new_level)

    # Catat perubahan
    if enabled:
        logging.getLogger("main").debug("✅ Debug mode ENABLED via admin panel.")
    else:
        logging.getLogger("main").info("ℹ️ Debug mode DISABLED via admin panel.")

