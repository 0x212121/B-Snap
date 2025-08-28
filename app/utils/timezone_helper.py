from datetime import datetime
from sqlalchemy.orm import Session
from app.models.config import Configuration
import pytz
from cachetools import TTLCache
import threading

# === Global cache untuk timezone ===
_tz_cache = TTLCache(maxsize=1, ttl=300)  # simpan 1 value, TTL = 300 detik (5 menit)
_tz_lock = threading.Lock()


def load_timezone_from_db(db: Session) -> str:
    config = db.query(Configuration).filter_by(key="timezone").first()
    if not config or not config.value:
        return "UTC"
    try:
        pytz.timezone(config.value)  # validasi
        return config.value
    except Exception as e:
        print(f"[Timezone] Invalid timezone in DB: {config.value} ({e})")
        return "UTC"


def get_current_timezone(db: Session) -> str:
    # pakai cache global biar ga query terus
    with _tz_lock:
        if "tz" in _tz_cache:
            return _tz_cache["tz"]

        tz_name = load_timezone_from_db(db)
        _tz_cache["tz"] = tz_name
        return tz_name


def to_current_timezone(dt: datetime, db: Session) -> datetime:
    tz_name = get_current_timezone(db)
    local_tz = pytz.timezone(tz_name)

    if not dt:
        return None

    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)

    return dt.astimezone(local_tz)


def format_datetime_with_tz(dt: datetime) -> str:
    if not dt or not dt.tzinfo:
        return "N/A"
    return dt.strftime("%d/%m/%Y - %H:%M:%S %Z")
