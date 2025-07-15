from datetime import datetime
from zoneinfo import ZoneInfo

WITA = ZoneInfo("Asia/Makassar")

def to_wita(dt: datetime) -> datetime:
    """Convert any datetime to WITA timezone (Asia/Makassar)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))  # treat as UTC if naive
    return dt.astimezone(WITA)

def format_wita(dt: datetime) -> str:
    """Format datetime to WITA time string."""
    try:
        local_dt = to_wita(dt)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S WITA")
    except Exception:
        return "Invalid time"
