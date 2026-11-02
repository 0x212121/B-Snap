"""Verify native GoWA HMAC signatures before parsing messages or accessing data."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re

from fastapi import HTTPException, Request

MAX_WEBHOOK_BYTES = 1024 * 1024


async def verified_gowa_payload(request: Request) -> object:
    """Authenticate the exact received bytes, with a bounded body and no secret logs."""
    secret = os.getenv("GOWA_WEBHOOK_SECRET", "")
    if len(secret) < 32 or not secret.strip():
        raise HTTPException(status_code=503, detail="Webhook authentication is not configured")
    signatures = request.headers.getlist("x-hub-signature-256")
    if len(signatures) != 1 or not re.fullmatch(r"sha256=[0-9a-f]{64}", signatures[0]):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Webhook body is too large")
        body.extend(chunk)
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signatures[0]):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
