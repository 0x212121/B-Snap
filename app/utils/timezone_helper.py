from datetime import datetime
from sqlalchemy.orm import Session
from app.models_sql import Configuration
import pytz

def get_current_timezone(db: Session) -> str:
    config = db.query(Configuration).filter_by(key="timezone").first()
    if not config or not config.value:
        return "UTC"
    try:
        # pastikan valid timezone
        pytz.timezone(config.value)
        return config.value
    except Exception as e:
        print(f"[Timezone] Invalid timezone in DB: {config.value} ({e})")
        return "UTC"


def to_current_timezone(dt: datetime, db: Session) -> datetime:
    tz_name = get_current_timezone(db)
    local_tz = pytz.timezone(tz_name)

    if dt.tzinfo is None:
        # Anggap datetime naive itu UTC
        dt = dt.replace(tzinfo=pytz.utc)
    else:
        # Convert dari timezone manapun ke lokal
        dt = dt.astimezone(pytz.utc)

    return dt.astimezone(local_tz)
