"""Regression coverage for heatmap timezone conversion and JSON responses."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.testclient import TestClient

from app.routes import stats


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """These tests mock database access and need no schema creation."""


@pytest.mark.parametrize(
    ("timezone_name", "expected_date", "expected_hour"),
    [
        ("Asia/Jakarta", "2026-10-07", 23),
        ("Asia/Makassar", "2026-10-08", 0),
        ("Asia/Jayapura", "2026-10-08", 1),
        ("UTC", "2026-10-07", 16),
    ],
)
def test_heatmap_uses_configured_timezone(
    monkeypatch: pytest.MonkeyPatch,
    timezone_name: str,
    expected_date: str,
    expected_hour: int,
) -> None:
    stamp = datetime(2026, 10, 7, 16, 30, tzinfo=UTC)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(timestamp=stamp),
        SimpleNamespace(timestamp=stamp.replace(tzinfo=None)),
    ]
    monkeypatch.setattr(stats, "get_current_timezone", lambda _db: timezone_name)
    app = FastAPI()
    app.include_router(stats.router)
    app.dependency_overrides[stats.get_db] = lambda: db
    app.dependency_overrides[stats.admin_access_required] = lambda: SimpleNamespace(id=1)

    route = next(route for route in stats.router.routes if route.path == "/stats/heatmap")
    assert route.response_class is ORJSONResponse
    with TestClient(app) as client:
        response = client.get("/stats/heatmap", params={"days": 7})
        invalid = client.get("/stats/heatmap", params={"days": 0})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert payload["labels"] == [f"{hour:02d}:00" for hour in range(24)]
    expected_counts = [0] * 24
    expected_counts[expected_hour] = 2
    assert payload["datasets"] == [{"label": expected_date, "data": expected_counts}]
    assert invalid.status_code == 422
