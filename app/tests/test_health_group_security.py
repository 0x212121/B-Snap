"""Health group boundaries across real queries, reports, progress, and workers."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import Base, get_db
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_daily_stats import CameraDailyStats
from app.models.camera_group import CameraGroup
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.health import CameraHealth
from app.models.health_check_status import HealthCheckStatus
from app.models.nvr import NVR
from app.models.sla_report import ScheduledReport
from app.models.task_timing import TaskTiming
from app.routes import auth, health
from app.utils import healthcheck
from app.utils.health_progress import health_progress


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full SQLite schema; these tests create only compatible tables."""


@pytest.fixture
def health_api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            model.__table__
            for model in (
                CameraGroup,
                Camera,
                NVR,
                CameraHealth,
                CameraDailyStats,
                CameraStatusChangeLog,
                HealthCheckStatus,
                ScheduledReport,
                TaskTiming,
            )
        ]
        + [camera_camera_groups],
    )
    with Session(engine) as db:
        allowed, denied = CameraGroup(id=101, name="Allowed"), CameraGroup(id=102, name="Denied")
        cams = [
            Camera(
                id="allowed",
                hostname="Allowed camera",
                ip="192.0.2.1",
                status="Active",
                groups=[allowed],
            ),
            Camera(
                id="shared",
                hostname="Shared camera",
                ip="192.0.2.2",
                status="Active",
                groups=[allowed, denied],
            ),
            Camera(
                id="denied",
                hostname="Denied camera",
                ip="192.0.2.3",
                status="Active",
                group_id=101,
                groups=[denied],
            ),
        ]
        nvrs = [
            NVR(
                id="nvr-allowed",
                hostname="Allowed NVR",
                ip="192.0.2.11",
                status="Active",
                group_id=101,
            ),
            NVR(
                id="nvr-denied",
                hostname="Denied NVR",
                ip="192.0.2.12",
                status="Active",
                group_id=102,
            ),
        ]
        db.add_all([allowed, denied, *cams, *nvrs])
        for camera in cams:
            db.add(
                CameraHealth(
                    camera_id=camera.id,
                    status="Online",
                    checked=datetime.now(UTC),
                    last_online=datetime.now(UTC),
                )
            )
            db.add(
                CameraDailyStats(
                    camera_id=camera.id,
                    camera_name=camera.hostname,
                    date=date(2026, 11, 1),
                    uptime_percentage=98,
                )
            )
        for nvr in nvrs:
            db.add(CameraHealth(nvr_id=nvr.id, status="Online", checked=datetime.now(UTC)))
        db.commit()
        user = SimpleNamespace(id=7, username="operator", role="operator", group_id=101)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="health-group-test")
        app.include_router(health.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: user
        renderer = Mock(return_value=JSONResponse({"rendered": True}))
        monkeypatch.setattr(health.templates, "TemplateResponse", renderer)
        monkeypatch.setattr(health, "get_current_timezone", lambda _db: "UTC")
        monkeypatch.setattr(health, "get_config", lambda _key, default=None: default)
        monkeypatch.setattr(
            health, "to_current_timezone", lambda value, _db: value.replace(tzinfo=UTC)
        )
        tasks = {
            name: Mock()
            for name in (
                "run_healthcheck_for_camera",
                "run_healthcheck_for_nvr",
                "run_healthcheck_for_all",
            )
        }
        for name, task in tasks.items():
            monkeypatch.setattr(health, name, task)
        with TestClient(app) as client:
            yield client, db, user, renderer, tasks
    engine.dispose()


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_health_status_and_uptime_use_association_membership(health_api, role):
    client, _db, user, _renderer, _tasks = health_api
    user.role = role
    response = client.get("/health/status")
    assert response.status_code == 200
    assert {row["id"] for row in response.json()["statuses"]} == {
        "allowed",
        "shared",
        "nvr-allowed",
    }
    assert response.json()["camera_online_count"] == 2
    assert response.json()["nvr_online_count"] == 1
    assert {row["camera_id"] for row in client.get("/health/uptime").json()["cameras"]} == {
        "allowed",
        "shared",
    }
    assert client.get("/health/uptime?camera_id=denied").status_code == 403


def test_global_operator_retains_all_devices(health_api):
    client, _db, user, _renderer, _tasks = health_api
    user.group_id = None
    assert len(client.get("/health/status").json()["statuses"]) == 5
    assert len(client.get("/health/uptime").json()["cameras"]) == 3


@pytest.mark.parametrize(("kind", "device"), [("camera", "denied"), ("nvr", "nvr-denied")])
def test_denied_trigger_has_no_background_network_work(health_api, kind, device):
    client, _db, _user, _renderer, tasks = health_api
    assert client.post(f"/health/trigger/{device}?type={kind}").status_code == 403
    for task in tasks.values():
        task.assert_not_called()


def test_allowed_trigger_passes_current_group_to_worker(health_api):
    client, _db, _user, _renderer, tasks = health_api
    assert client.post("/health/trigger/shared?type=camera").status_code == 200
    tasks["run_healthcheck_for_camera"].assert_called_once_with("shared", 101)
    assert client.post("/health/trigger/allowed?type=invalid").status_code == 422


def test_bulk_progress_is_group_scoped_and_not_global(health_api):
    client, db, user, _renderer, tasks = health_api
    global_tracker = health_progress(db, None, create=True)
    global_tracker.is_running = True
    global_tracker.total_cameras = 500
    db.commit()
    assert client.get("/health/status/check").json() == {"is_complete": True}
    assert client.post("/health/trigger_all").status_code == 200
    tasks["run_healthcheck_for_all"].assert_called_once_with(101)
    assert client.get("/health/status/check").json()["progress"]["total"] == 3
    assert client.post("/health/trigger_all").status_code == 409
    user.group_id = 102
    assert client.get("/health/status/check").json() == {"is_complete": True}
    user.group_id = None
    assert client.get("/health/status/check").json()["progress"]["total"] == 500


def test_history_filters_rows_counts_groups_and_search(health_api):
    client, _db, _user, renderer, _tasks = health_api
    assert client.get("/health/history").status_code == 200
    context = renderer.call_args.args[1]
    assert len(context["historical_data"]) == 2
    assert context["pagination"]["total"] == 2
    assert [group.id for group in context["camera_groups"]] == [101]
    assert client.get("/health/history?q=Denied").status_code == 200
    assert renderer.call_args.args[1]["historical_data"] == []
    assert renderer.call_args.args[1]["pagination"]["total"] == 0


def test_sla_and_export_and_generated_report_do_not_include_denied_camera(health_api):
    client, _db, _user, _renderer, _tasks = health_api
    dates = {"start_date": "2026-11-01", "end_date": "2026-11-02"}
    response = client.get(
        "/health/sla-report", params=dates, headers={"Accept": "application/json"}
    )
    assert response.status_code == 200
    assert response.json()["summary"]["total_cameras"] == 2
    assert {row["camera_id"] for row in response.json()["cameras"]} == {"allowed", "shared"}
    csv = client.get("/health/history/export", params={**dates, "format": "csv"})
    assert csv.status_code == 200
    assert "Denied camera" not in csv.text
    assert "Allowed camera" in csv.text
    generated = client.post(
        "/health/history/report", data={**dates, "format": "csv", "include_charts": "false"}
    )
    assert generated.status_code == 200
    assert "Denied camera" not in generated.text
    assert "Allowed camera" in generated.text
    injected = client.post(
        "/health/history/report",
        data={**dates, "format": "csv", "include_charts": "false", "groups": "Denied"},
    )
    assert injected.status_code == 200
    assert "Denied camera" not in injected.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health/reports/scheduled"),
        ("POST", "/health/reports/scheduled"),
        ("DELETE", "/health/reports/scheduled/other"),
    ],
)
def test_global_scheduled_reports_cannot_be_managed_by_restricted_operator(
    health_api, method, path
):
    client, db, _user, _renderer, _tasks = health_api
    assert (
        client.request(
            method, path, data={"name": "Report", "recipients": "test@example.com"}
        ).status_code
        == 403
    )
    assert db.query(ScheduledReport).count() == 0


def test_scoped_bulk_worker_only_pings_current_members(health_api, monkeypatch):
    _client, db, _user, _renderer, _tasks = health_api
    seen = []
    monkeypatch.setattr(healthcheck, "SessionLocal", lambda: Session(db.get_bind()))
    monkeypatch.setattr(
        healthcheck,
        "_perform_and_update_health_check",
        lambda _db, device: seen.append(device["id"]),
    )
    cleanup = Mock()
    monkeypatch.setattr(healthcheck, "cleanup_old_email_logs", cleanup)
    healthcheck.run_healthcheck_for_all(101)
    assert set(seen) == {"allowed", "shared", "nvr-allowed"}
    cleanup.assert_not_called()
    db.expire_all()
    tracker = health_progress(db, 101)
    assert not tracker.is_running
    assert tracker.completed_cameras == 3
    assert db.get(HealthCheckStatus, 1) is None


def test_individual_workers_recheck_membership_before_ping(health_api, monkeypatch):
    _client, db, _user, _renderer, _tasks = health_api
    monkeypatch.setattr(healthcheck, "SessionLocal", lambda: Session(db.get_bind()))
    ping = Mock()
    monkeypatch.setattr(healthcheck, "_perform_and_update_health_check", ping)
    healthcheck.run_healthcheck_for_camera("denied", 101)
    healthcheck.run_healthcheck_for_nvr("nvr-denied", 101)
    ping.assert_not_called()
