"""Restrict recording completion events to the initiating browser user."""

from __future__ import annotations

import asyncio

from typing import Any

from fastapi import WebSocket

from app.db.database import SessionLocal
from app.models.user import User
from app.utils.auth_token import is_valid_web_token


async def deliver_notification(websocket: WebSocket, payload: dict[str, Any], message: str) -> None:
    """Filter private recording events before sending any contents to the browser."""
    if payload.get("type") == "record_complete" and not await asyncio.to_thread(
        can_receive_recording, websocket, payload
    ):
        return
    await websocket.send_text(message)


def can_receive_recording(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    """Validate the recipient against a live browser login, including token revocation."""
    recipient = payload.get("recipient_user_id")
    session = websocket.scope.get("session", {})
    token = websocket.cookies.get("session_token")
    if (
        payload.get("source") != "browser"
        or not isinstance(recipient, int)
        or session.get("user_id") != recipient
        or not token
    ):
        return False

    try:
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == recipient).first()
            return bool(user and is_valid_web_token(token, user.web_tokens))
    except Exception:
        # A failed validation must never broadcast a private event.
        return False
