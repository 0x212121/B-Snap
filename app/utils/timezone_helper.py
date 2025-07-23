from datetime import datetime
from sqlalchemy.orm import Session
from app.models_sql.configuration import Configuration
import pytz

def get_current_timezone(db: Session) -> str:
    config = db.query(Configuration).filter_by(key="timezone").first()
    return config.value if config else "UTC"

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
