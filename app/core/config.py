from app.models_sql import Configuration
from app.db.database import SessionLocal


def get_config(key: str, default=None):
    db = SessionLocal()
    config = db.query(Configuration).filter_by(key=key).first()
    db.close()

    if not config:
        return default

    value = config.value
    if default is None:
        return value  # return str by default
    try:
        return type(default)(value)
    except (ValueError, TypeError):
        return default  # fallback ke default jika konversi gagal
