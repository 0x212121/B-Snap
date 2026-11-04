"""Validation for the configured browser-accessible B-Snap URL."""

from __future__ import annotations

from urllib.parse import urlsplit


def normalize_public_url(value: str) -> str:
    """Accept an optional HTTP(S) base URL without credentials, query, or fragment."""
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
            and "\\" not in value
        )
        _port = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Public B-Snap URL must be HTTP(S), without credentials, query, or fragment"
        )
    return value
