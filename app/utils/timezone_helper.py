from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.config import Configuration
import pytz
from functools import lru_cache

@lru_cache(maxsize=32)
def _tz(tz_name: str):
    try:
        return pytz.timezone(tz_name)
    except:
        return pytz.UTC

def _load_tz_name(db: Session):
    cfg = db.query(Configuration).filter_by(key="timezone").first()
    return cfg.value if cfg else "UTC"

def get_current_timezone(db: Session) -> str:
    """Return full timezone name e.g. 'Asia/Makassar'."""
    try:
        tz = _load_tz_name(db)
        pytz.timezone(tz)  # validate
        return tz
    except:
        return "UTC"

def get_timezone_display(db: Session) -> str:
    """Alias untuk template yang butuh format Asia/Makassar."""
    return get_current_timezone(db)

def clear_timezone_cache():
    _tz.cache_clear()

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def to_current_timezone(dt: datetime, db: Session) -> datetime:
    if not dt:
        return None
    tz = _tz(get_current_timezone(db))
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(tz)

def format_datetime_with_tz(dt: datetime, tz_name: str = None) -> str:
    """Backward compatible."""
    if not dt or not dt.tzinfo:
        return "N/A"
    if tz_name:
        dt = dt.astimezone(_tz(tz_name))
    return dt.strftime(f"%d/%m/%Y - %H:%M:%S {dt.tzname() or 'UTC'}")

def format_datetime_standard(dt: datetime, db: Session = None) -> str:
    """Format dengan timezone yang benar untuk display."""
    if not dt:
        return "N/A"
    if db:
        dt = to_current_timezone(dt, db)
    elif not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    # Format: 30/03/2026 - 16:54:00 WITA (otomatis dari pytz)
    return dt.strftime("%d/%m/%Y - %H:%M:%S %Z")

def format_date_standard(dt) -> str:
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except:
            return dt
    return dt.strftime("%d/%m/%Y")

def format_datetime_iso(dt: datetime) -> str:
    if not dt:
        return None
    if not dt.tzinfo:
        dt = pytz.utc.localize(dt)
    return dt.isoformat()

def parse_datetime_standard(dt_str: str) -> datetime:
    if not dt_str:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", 
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
        try:
            return datetime.strptime(dt_str, fmt)
        except:
            continue
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except:
        return None