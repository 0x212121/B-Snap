from datetime import datetime
from sqlalchemy.orm import Session
from app.models_sql import Configuration
import pytz

def get_current_timezone(db: Session) -> str:
    config = db.query(Configuration).filter_by(key="timezone").first()
    if not config or not config.value:
        return "UTC"
    try:
        pytz.timezone(config.value)
        return config.value
    except Exception as e:
        print(f"[Timezone] Invalid timezone in DB: {config.value} ({e})")
        return "UTC"


def to_current_timezone(dt: datetime, db: Session) -> datetime:
    tz_name = get_current_timezone(db)
    local_tz = pytz.timezone(tz_name)

    if not dt:
        return None

    if dt.tzinfo is None:
        # Anggap datetime naive sebagai UTC
        dt = pytz.utc.localize(dt)
    
    return dt.astimezone(local_tz)

def format_datetime_with_tz(dt: datetime) -> str:
    """
    Memformat objek datetime yang sadar zona waktu menjadi string standar
    termasuk singkatan zona waktu (misalnya, WITA, WIB).
    """
    if not dt or not dt.tzinfo:
        return "N/A"
    # %Z akan secara otomatis menampilkan singkatan yang benar (WITA, WIB, dll.)
    return dt.strftime("%d/%m/%Y - %H:%M:%S %Z")