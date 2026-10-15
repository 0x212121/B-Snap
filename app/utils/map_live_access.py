"""Database-backed live-view policy shared by Maps and streaming requests."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.config import Configuration
from app.models.user import User

DEFAULT_MAP_LIVE_ENABLED = "1"
LIVE_POLICY_INTERVAL_SECONDS = 5


def map_live_enabled(db: Session) -> bool:
    """Read the saved toggle without caching or overriding user configuration."""
    setting = db.query(Configuration).filter_by(key="map_live_enabled").first()
    return (setting.value if setting is not None else DEFAULT_MAP_LIVE_ENABLED) == "1"


def live_policy_allows(user_id: int) -> bool:
    """Recheck enablement and administrator role for an ongoing stream."""
    with SessionLocal() as db:
        return (
            map_live_enabled(db)
            and db.query(User.role).filter(User.id == user_id).scalar() == "admin"
        )
