"""Brand identity settings and their safe defaults."""

from __future__ import annotations

import logging
import re

from pathlib import Path

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.config import Configuration

logger = logging.getLogger(__name__)

DEFAULT_BRANDING = {
    "name": "B-Snap",
    "tagline": "",
    "logo": "/static/icons/b-snap.png",
    "palette": "blue",
}
BRANDING_KEYS = {
    "name": "branding_name",
    "tagline": "branding_tagline",
    "logo": "branding_logo",
    "palette": "branding_palette",
}
PALETTES = {"blue", "teal", "violet", "orange"}
LOGOS = {
    "/static/icons/b-snap.png",
    "/static/icons/logo.png",
    "/static/icons/logo.ico",
    "/static/branding/logo.png",
    "/static/branding/logo.ico",
}


def is_application_logo_url(value: str) -> bool:
    """Allow bundled/legacy logos and content-addressed application uploads only."""
    return value in LOGOS or bool(
        re.fullmatch(r"/static/branding/application-[a-f0-9]{64}\.(?:png|ico)", value)
    )


def branding_logo_choices(branding: dict[str, str]) -> list[dict[str, str]]:
    """Offer the default, available application uploads and the current selection."""
    choices = [{"value": DEFAULT_BRANDING["logo"], "label": "B-Snap default"}]
    for url, label in (
        ("/static/branding/logo.png", "Uploaded PNG"),
        ("/static/branding/logo.ico", "Uploaded ICO"),
        (branding["logo"], "Current logo"),
    ):
        if url not in {choice["value"] for choice in choices} and Path(url.lstrip("/")).is_file():
            choices.append({"value": url, "label": label})
    return choices


def validate_branding(name: str, tagline: str, logo: str, palette: str = "blue") -> dict[str, str]:
    """Validate administrator-provided branding without accepting arbitrary URLs."""
    name, tagline = name.strip(), tagline.strip()
    if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
        raise ValueError("Application name must be 1 to 80 characters on one line.")
    if len(tagline) > 160 or any(ord(char) < 32 for char in tagline):
        raise ValueError("Tagline must be at most 160 characters on one line.")
    if not is_application_logo_url(logo):
        raise ValueError("Choose a supported logo.")
    if logo != DEFAULT_BRANDING["logo"] and not Path(logo.lstrip("/")).is_file():
        raise ValueError("The selected logo is unavailable. Upload a logo or choose the default.")
    if palette not in PALETTES:
        raise ValueError("Choose a supported color palette.")
    return {"name": name, "tagline": tagline, "logo": logo, "palette": palette}


def read_branding(db: Session) -> dict[str, str]:
    """Read branding with per-field fallbacks for missing or invalid values."""
    rows = db.query(Configuration).filter(Configuration.key.in_(BRANDING_KEYS.values())).all()
    values = {row.key: row.value for row in rows}
    name = values.get(BRANDING_KEYS["name"]) or DEFAULT_BRANDING["name"]
    tagline = values.get(BRANDING_KEYS["tagline"]) or ""
    logo = values.get(BRANDING_KEYS["logo"]) or DEFAULT_BRANDING["logo"]
    palette = values.get(BRANDING_KEYS["palette"]) or DEFAULT_BRANDING["palette"]
    result = DEFAULT_BRANDING.copy()
    for field, value in {
        "name": name,
        "tagline": tagline,
        "logo": logo,
        "palette": palette,
    }.items():
        candidate = {**result, field: value}
        try:
            result = validate_branding(**candidate)
        except (ValueError, TypeError, AttributeError):
            logger.warning("Invalid branding %s; using default", field)
    return result


def get_branding() -> dict[str, str]:
    """Load branding for a render, including setup and database failure fallback."""
    db = None
    try:
        db = SessionLocal()
        return read_branding(db)
    except Exception:
        logger.warning("Unable to load branding; using defaults")
        return DEFAULT_BRANDING.copy()
    finally:
        if db is not None:
            db.close()
