"""Camera-scoped, short-lived bearer grants for public live previews."""

from __future__ import annotations

import hashlib
import re
import secrets
import time

from typing import Any

from fastapi import HTTPException
from itsdangerous import BadData, URLSafeSerializer

from app.core.security import load_security_settings

DEFAULT_LIVE_LINK_SECONDS = 300
MAX_LIVE_LINK_SECONDS = 900
PUBLIC_LIVE_PATH = re.compile(r"/public/cameras/[a-zA-Z0-9_-]{1,100}/live")
_LINK_TOKEN = re.compile(r"(/public/cameras/[a-zA-Z0-9_-]{1,100}/live\?token=)[a-zA-Z0-9_.-]+")


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(
        load_security_settings().secret_key,
        salt="bsnap-public-camera-live-v1",
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def create_live_grant(
    camera_id: str, issuer_id: int, sender_id: int, seconds: int
) -> tuple[str, int]:
    """Issue a signed capability for a single camera and bounded expiry."""
    if not 30 <= seconds <= MAX_LIVE_LINK_SECONDS:
        raise ValueError("Live links must expire in 30 to 900 seconds")
    now = int(time.time())
    expires = now + seconds
    return (
        _serializer().dumps(
            {
                "camera": camera_id,
                "issuer": issuer_id,
                "sender": sender_id,
                "issued": now,
                "expires": expires,
                "nonce": secrets.token_urlsafe(16),
            }
        ),
        expires,
    )


def verify_live_grant(camera_id: str, token: str) -> dict[str, Any]:
    """Reject expired, altered, cross-camera, or malformed capabilities."""
    try:
        if not token or len(token) > 2000:
            raise ValueError
        grant = _serializer().loads(token)
        if not isinstance(grant, dict) or grant.get("camera") != camera_id:
            raise ValueError
        if any(
            type(grant.get(key)) is not int for key in ("issuer", "sender", "issued", "expires")
        ):
            raise ValueError
        now = time.time()
        if not grant["issued"] <= now < grant["expires"]:
            raise ValueError
        if not 30 <= grant["expires"] - grant["issued"] <= MAX_LIVE_LINK_SECONDS:
            raise ValueError
        return grant
    except (BadData, ValueError, TypeError):
        raise HTTPException(status_code=403, detail="Live link is invalid or expired") from None


def redact_live_link_tokens(value: str) -> str:
    """Hide bearer capabilities in application logs and message-monitor records."""
    return _LINK_TOKEN.sub(r"\1[REDACTED]", value)
