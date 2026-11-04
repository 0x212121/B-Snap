"""Signed webhook delivery with real whitelist checks and isolated persistence."""

from __future__ import annotations

import hashlib
import hmac
import json

from unittest.mock import Mock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.models.camera_group import CameraGroup
from app.models.config import Configuration
from app.models.log import CommandLog, WhatsAppMessageLog
from app.models.whitelist import WhatsappWhitelist
from app.routes import wa_webhook


@pytest.mark.parametrize("stored_phone", ["628123456789", "+628123456789"])
@pytest.mark.parametrize("sender", ["628123456789:12@s.whatsapp.net", "+628123456789"])
@pytest.mark.parametrize("nested", [False, True])
def test_signed_whitelisted_message_is_logged_and_answered(
    monkeypatch, stored_phone: str, sender: str, nested: bool
) -> None:
    """Changing storage to E.164 must preserve delivery, replies, and deduplication."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            WhatsappWhitelist.__table__,
            Configuration.__table__,
            CommandLog.__table__,
            WhatsAppMessageLog.__table__,
        ],
    )
    secret = "test-only-whitelist-delivery-signing-key"
    monkeypatch.setenv("GOWA_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        wa_webhook,
        "get_command_settings",
        lambda _db: [{"trigger": "/ping", "action": "text", "response": "Pong", "enabled": True}],
    )
    gateway = Mock()
    gateway.send_text.return_value = {"success": True}
    monkeypatch.setattr(wa_webhook, "WAGatewayService", lambda _db: gateway)
    event = {
        "from": sender,
        "body": "/ping",
        "id": "unique-inbound-message",
        "chat_id": "628123456789@s.whatsapp.net",
    }
    payload = {"event": "message", "payload": event} if nested else event
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    try:
        with Session(engine) as db:
            db.add(WhatsappWhitelist(phone_number=stored_phone))
            db.commit()
            app = FastAPI()
            app.include_router(wa_webhook.router)
            app.dependency_overrides[get_db] = lambda: db
            with TestClient(app) as client:
                for _ in range(2):
                    response = client.post(
                        "/webhook/gowa",
                        content=raw,
                        headers={"X-Hub-Signature-256": signature},
                    )
                    assert response.status_code == 200
            messages = db.query(WhatsAppMessageLog).order_by(WhatsAppMessageLog.id).all()
            assert [(item.direction, item.status, item.message) for item in messages] == [
                ("inbound", "received", "/ping"),
                ("outbound", "accepted", "Pong"),
            ]
            assert all(item.phone_number == "628123456789" for item in messages)
            assert db.query(CommandLog).count() == 1
            gateway.send_text.assert_called_once_with(event["chat_id"], "Pong", event["id"])
    finally:
        engine.dispose()
