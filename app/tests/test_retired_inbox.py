"""Removal of the unused inbox while retaining live toast and email-log behavior."""

from __future__ import annotations

import asyncio
import json

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.db.database import Base, get_db
from app.models.camera_email_notification_log import (
    CameraEmailNotificationLog,
    CameraEmailNotificationRecipient,
)
from app.routes import email_logs
from app.routes.auth import admin_access_required
from app.utils import notification_service
from app.ws import record_notifications


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; each test owns mocks or compatible tables."""


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/notifications/"),
        ("GET", "/api/notifications/unread-count"),
        ("POST", "/api/notifications/1/read"),
        ("POST", "/api/notifications/mark-all-read"),
        ("DELETE", "/api/notifications/1"),
        ("DELETE", "/api/notifications/"),
    ],
)
def test_retired_endpoints_are_absent_from_production_routes(method, path):
    # Use production routes without starting DB listeners or auth/setup middleware.
    app = FastAPI()
    app.router.routes.extend(main.app.routes)
    with TestClient(app) as client:
        assert client.request(method, path).status_code == 404
    assert "notifications" not in Base.metadata.tables
    assert "notification_queue" in Base.metadata.tables


def test_live_toast_still_uses_authenticated_delivery_without_inbox_storage(monkeypatch):
    socket = MagicMock()
    socket.scope = {"session": {"user_id": 7}}
    socket.cookies = {"session_token": "test-browser-token"}
    socket.send_text = AsyncMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        group_id=None,
        web_tokens=[
            {
                "token": "test-browser-token",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ],
    )
    monkeypatch.setattr(record_notifications, "SessionLocal", factory)
    monkeypatch.setattr(notification_service, "websocket_connections", {socket})
    toast = asyncio.run(
        notification_service.NotificationService.send_toast(
            "Live only", user_id=7, camera_id="550e8400-e29b-41d4-a716-446655440000", duration=0
        )
    )
    socket.send_text.assert_awaited_once()
    payload = json.loads(socket.send_text.await_args.args[0])
    assert payload["data"]["message"] == "Live only"
    assert payload["data"]["duration"] == 0
    assert payload["data"]["camera_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert toast.duration == 0


def test_email_logs_still_read_their_independent_delivery_tables(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[CameraEmailNotificationLog.__table__, CameraEmailNotificationRecipient.__table__],
    )
    with Session(engine) as db:
        log = CameraEmailNotificationLog(
            camera_name="Synthetic camera",
            incident_started_at=datetime.now(UTC),
            sent_at=datetime.now(UTC),
            success=True,
            reason="offline",
        )
        log.recipients = [
            CameraEmailNotificationRecipient(recipient_email="recipient@example.test")
        ]
        db.add(log)
        db.commit()
        app = FastAPI()
        app.include_router(email_logs.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(
            id=7, role="admin"
        )
        monkeypatch.setattr(email_logs, "get_current_timezone", lambda _db: "UTC")
        monkeypatch.setattr(
            email_logs, "format_datetime_standard", lambda value, **_kwargs: value.isoformat()
        )
        with TestClient(app) as client:
            response = client.get("/api/email-logs")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["logs"][0]["recipients"] == ["recipient@example.test"]
        assert response.json()["logs"][0]["success"] is True
    engine.dispose()
