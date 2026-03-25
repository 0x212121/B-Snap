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
    """Format datetime to standard display format with timezone."""
    if not dt or not dt.tzinfo:
        return "N/A"
    return dt.strftime("%d/%m/%Y - %H:%M:%S %Z")


def format_datetime_standard(dt: datetime, db: Session = None) -> str:
    """
    Standard datetime formatter used across the application.
    Converts to current timezone and formats consistently.
    """
    if not dt:
        return "N/A"
    
    if db and (not dt.tzinfo or dt.tzinfo.utcoffset(dt) is None):
        dt = to_current_timezone(dt, db)
    elif not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    
    return dt.strftime("%d/%m/%Y - %H:%M:%S %Z")


def format_date_standard(dt: datetime) -> str:
    """Standard date-only formatter."""
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%d/%m/%Y")


def format_datetime_iso(dt: datetime) -> str:
    """ISO format for API responses (always in UTC)."""
    if not dt:
        return None
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    return dt.isoformat()


def parse_datetime_standard(dt_str: str) -> datetime:
    """Parse datetime from various standard formats."""
    if not dt_str:
        return None
    
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y - %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    # Try ISO format as fallback
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        return None
