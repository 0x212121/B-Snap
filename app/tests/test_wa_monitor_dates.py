"""Message monitor timezone contract without production DB or message delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.routes import wa_webhook
from app.routes.auth import admin_access_required


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; only isolated query mocks are used."""


@pytest.mark.parametrize(
    ("zone", "label"),
    [
        ("Asia/Jakarta", "WIB"),
        ("Asia/Makassar", "WITA"),
        ("Asia/Jayapura", "WIT"),
        ("Europe/London", "Europe/London"),
    ],
)
def test_monitor_supplies_zone_and_explicit_timestamp_offsets(monkeypatch, zone, label):
    values = [
        datetime(2026, 11, 2, 16, 30),
        datetime(2026, 11, 3, 0, 30, tzinfo=timezone(timedelta(hours=8))),
        None,
    ]
    rows = [
        SimpleNamespace(
            id=index,
            timestamp=value,
            phone_number="synthetic",
            direction="inbound",
            status="received",
            command="/help",
            message="synthetic",
            error=None,
        )
        for index, value in enumerate(values)
    ]
    db = Mock()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    monkeypatch.setattr(wa_webhook, "get_current_timezone", lambda _db: zone)
    app = FastAPI()
    app.include_router(wa_webhook.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(id=7, role="admin")
    with TestClient(app) as client:
        response = client.get("/api/admin/wa-bot/messages?limit=1000")
    assert response.status_code == 200
    data = response.json()
    assert data["timezone"] == zone
    assert data["timezone_label"] == label
    for item in data["messages"][:2]:
        timestamp = datetime.fromisoformat(item["timestamp"])
        assert timestamp.tzinfo is not None
        assert timestamp.astimezone(UTC) == datetime(2026, 11, 2, 16, 30, tzinfo=UTC)
    assert data["messages"][2]["timestamp"] is None
    db.query.return_value.order_by.return_value.limit.assert_called_once_with(250)
