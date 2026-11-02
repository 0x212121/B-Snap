"""Validate stored web-session tokens without trusting their JSON shape."""

from __future__ import annotations

import json

from datetime import UTC, datetime


def is_valid_web_token(token: str, token_list: object) -> bool:
    """Reject malformed/expired tokens; interpret legacy naive expiry as UTC."""
    if not token:
        return False
    if isinstance(token_list, str):
        try:
            token_list = json.loads(token_list)
        except (ValueError, TypeError):
            return False
    if not isinstance(token_list, list):
        return False
    now = datetime.now(UTC)
    for item in token_list:
        if not isinstance(item, dict) or item.get("token") != token:
            continue
        if _has_future_expiry(item.get("expires_at"), now):
            return True
    return False


def _has_future_expiry(expiry: object, now: datetime) -> bool:
    if not isinstance(expiry, str):
        return False
    try:
        expires = datetime.fromisoformat(expiry)
    except ValueError:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > now
