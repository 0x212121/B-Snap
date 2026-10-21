"""Analytics query budgets and response compatibility on isolated SQLite tables."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.api_daily_stats import ApiDailyStats
from app.models.camera_daily_stats import CameraDailyStats
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.models.camera_group import CameraGroup
from app.models.config import Configuration
from app.models.log import ApiLog, CommandLog
from app.models.task_timing import TaskTiming
from app.routes import insights, stats
from app.utils.template_helper import templates


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema fixture; only compatible tables are needed."""


AnalyticsDB = tuple[Session, list[str]]


@pytest.fixture
def analytics_db() -> Generator[AnalyticsDB, None, None]:
    """Keep all writes isolated from the application database."""
    engine = create_engine("sqlite:///:memory:")
    models = (
        Configuration,
        ApiLog,
        ApiDailyStats,
        CommandLog,
        CameraEmailNotificationLog,
        CameraDailyStats,
        TaskTiming,
        CameraGroup,
    )
    Base.metadata.create_all(engine, tables=[model.__table__ for model in models])
    queries: list[str] = []

    @event.listens_for(engine, "after_cursor_execute")
    def record_query(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        queries.append(statement)

    try:
        with Session(engine) as db:
            db.add(Configuration(key="timezone", value="Asia/Makassar"))
            db.commit()
            queries.clear()
            yield db, queries
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "zone", ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "America/New_York"]
)
@pytest.mark.parametrize("populated", [False, True])
def test_dashboard_reuses_daily_totals_and_preserves_api_shape(
    analytics_db: AnalyticsDB, zone: str, populated: bool
) -> None:
    """Keep totals, deltas, rankings, and archived API counts while avoiding repeat scans."""
    db, queries = analytics_db
    db.get(Configuration, "timezone").value = zone
    if populated:
        for value in (
            "2026-09-29T16:30:00+00:00",
            "2026-09-30T16:30:00+00:00",
            "2026-10-02T15:30:00+00:00",
        ):
            timestamp = datetime.fromisoformat(value)
            db.add(ApiLog(timestamp=timestamp, endpoint="/api/test"))
            db.add(CommandLog(timestamp=timestamp, command="snapshot", source="whatsapp"))
            for success in (True, False):
                db.add(
                    CameraEmailNotificationLog(
                        camera_name="Camera <one>",
                        sent_at=timestamp,
                        incident_started_at=timestamp,
                        success=success,
                    )
                )
        db.add(
            CommandLog(
                timestamp=datetime(2026, 10, 1, tzinfo=UTC),
                command="gowa_event:message",
                source="whatsapp_webhook",
            )
        )
        db.add(
            ApiDailyStats(
                date=datetime(2026, 10, 1).date(), timezone="Asia/Makassar", request_count=12
            )
        )
    db.commit()
    expected = {
        "summary": insights.get_summary(db, "2026-10-01", "2026-10-02", None),
        "daily": insights.get_daily_stats(db, "2026-10-01", "2026-10-02", None),
        "top_commands": insights.get_top_commands(db, "2026-10-01", "2026-10-02", 10, None),
        "top_cameras": insights.get_top_cameras_by_email(db, "2026-10-01", "2026-10-02", 5, None),
    }
    queries.clear()
    result = insights.get_dashboard_stats(db, "2026-10-01", "2026-10-02", None)
    assert result == expected
    assert len(queries) == 11  # SQLite uses two statements per API aggregate; PostgreSQL uses one.
    assert sum("FROM configurations" in sql for sql in queries) == 1
    assert sum("FROM api_logs" in sql for sql in queries) == 2
    for key, rows in result["daily"].items():
        assert result["summary"]["totals"][key] == sum(row["count"] for row in rows)


def test_analytics_shell_does_not_load_snapshot_data(analytics_db: AnalyticsDB) -> None:
    """Overview startup must avoid stats, health, and per-camera or per-log queries."""
    db, queries = analytics_db
    with patch.object(
        stats.templates, "TemplateResponse", side_effect=lambda _name, context: context
    ):
        result = stats.get_analytics(None, None, None, 14, db, None)
    assert result["selected_days"] == 14
    assert len(queries) == 2
    assert all("camera_daily_stats" not in sql and "task_timings" not in sql for sql in queries)


@pytest.mark.parametrize("count", [0, 1, 10, 20])
@pytest.mark.parametrize(
    ("zone", "label"),
    [("Asia/Jakarta", "WIB"), ("Asia/Makassar", "WITA"), ("Asia/Jayapura", "WIT")],
)
def test_snapshot_history_query_count_is_constant(
    analytics_db: AnalyticsDB, count: int, zone: str, label: str
) -> None:
    """Format naive UTC and aware history near midnight without per-row config reads."""
    db, queries = analytics_db
    db.get(Configuration, "timezone").value = zone
    for _index in range(count):
        db.add(
            TaskTiming(
                task_name="scheduled_snapshot",
                started_at=datetime(2026, 10, 1, 16, 30),
                ended_at=datetime(2026, 10, 1, 16, 31, 1, tzinfo=UTC),
                duration_ms=61000,
                status="success",
            )
        )
    db.commit()
    queries.clear()
    result = stats.get_snapshot_details(db, days=14, group_id=None)
    assert len(queries) == 3
    assert sum("FROM configurations" in sql for sql in queries) == 1
    assert len(result["timing_logs"]) == min(count, 10)
    for row in result["timing_logs"]:
        assert row["duration"] == "00:01:01"
        assert row["started_at"].endswith(label)
        expected_date = "01/10/2026" if zone == "Asia/Jakarta" else "02/10/2026"
        assert row["started_at"].startswith(expected_date)


def test_snapshot_details_rejects_missing_group(analytics_db: AnalyticsDB) -> None:
    """Retain the same group validation as other admin analytics endpoints."""
    db, _queries = analytics_db
    with pytest.raises(stats.HTTPException) as error:
        stats.get_snapshot_details(db, days=7, group_id=9999)
    assert error.value.status_code == 404


@pytest.mark.parametrize("status_code", [401, 403])
def test_snapshot_details_keeps_admin_access_dependency(status_code: int) -> None:
    """The lazy endpoint must enforce authentication before accessing analytics data."""
    app = FastAPI()
    app.include_router(stats.router)

    def deny_access() -> None:
        raise HTTPException(status_code=status_code)

    app.dependency_overrides[stats.admin_access_required] = deny_access
    with TestClient(app) as client:
        response = client.get("/stats/snapshot-details")
    assert response.status_code == status_code


@pytest.mark.parametrize(
    "scenario",
    [
        "activity_first",
        "operations_first",
        "partial_failure",
        "activity_failure",
        "stale",
        "snapshots",
    ],
)
def test_analytics_frontend_loading(scenario: str) -> None:
    """Execute real page JS with delayed responses and isolated DOM/network objects."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the frontend regression check")
    root = Path(__file__).resolve().parents[2]
    source = (root / "templates/analytics.html").read_text(encoding="utf-8")
    script = templates.env.from_string(
        "\n".join(re.findall(r"<script>(.*?)</script>", source, re.S))
    ).render(
        server_timezone="Asia/Makassar",
    )
    result = subprocess.run(
        [node, str(Path(__file__).with_name("analytics_dashboard_harness.cjs"))],
        input=json.dumps({"script": script, "scenario": scenario}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
