"""Historical heatmap boundaries and real group restrictions in isolated tables."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.camera_status_change_log import CameraStatusChangeLog
from app.models.config import Configuration
from app.routes import map_heatmap
from app.utils.map_heatmap import calculate_downtime

NOW = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)
START = NOW - timedelta(days=1)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema setup: these tests create only compatible tables."""


@pytest.mark.parametrize(
    ("transitions", "expected"),
    [
        ([], (0, 0)),
        ([(-2, "Offline")], (86400, 86400)),
        ([(-2, "Offline"), (2, "Online")], (7200, 86400)),
        ([(0, "Online"), (3, "Offline"), (5, "High Latency")], (7200, 86400)),
        ([(3, "Offline")], (75600, 75600)),
        ([(0, "Maintenance"), (8, "Standalone"), (10, "High Latency")], (0, 86400)),
        ([(0, "Offline"), (24, "Online")], (86400, 86400)),
        ([(-4, "Offline"), (-1, "Online"), (24, "Offline")], (0, 86400)),
    ],
)
def test_clipped_offline_intervals(
    transitions: list[tuple[int, str]], expected: tuple[int, int]
) -> None:
    events = [
        CameraStatusChangeLog(changed_at=START + timedelta(hours=hour), new_status=status)
        for hour, status in transitions
    ]
    assert calculate_downtime(events, START, NOW) == expected


def test_naive_database_times_are_utc_and_offsets_do_not_shift_incidents() -> None:
    events = [
        CameraStatusChangeLog(changed_at=START.replace(tzinfo=None), new_status="Offline"),
        CameraStatusChangeLog(
            changed_at=(START + timedelta(hours=2)).astimezone(timezone(timedelta(hours=8))),
            new_status="Online",
        ),
    ]
    assert calculate_downtime(events, START, NOW) == (7200, 86400)


@pytest.fixture
def heatmap_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            CameraStatusChangeLog.__table__,
            Configuration.__table__,
        ],
    )
    with Session(engine) as db:
        first = CameraGroup(id=101, name="Site A")
        second = CameraGroup(id=102, name="Site B")
        cameras = [
            Camera(
                id="shared",
                hostname="shared",
                status="Active",
                groups=[first, second],
                group=second,
                latitude=0,
                longitude=117,
            ),
            Camera(
                id="other",
                hostname="other",
                status="Active",
                groups=[second],
                latitude=1,
                longitude=118,
            ),
            Camera(
                id="no-history", hostname="no-history", status="Active", latitude=2, longitude=119
            ),
            Camera(id="no-gps", hostname="no-gps", status="Active", groups=[first]),
            Camera(
                id="maintenance",
                hostname="maintenance",
                status="Maintenance",
                groups=[first],
                latitude=1,
                longitude=118,
            ),
            Camera(
                id="standalone",
                hostname="standalone",
                status="Standalone",
                groups=[first],
                latitude=1,
                longitude=118,
            ),
        ]
        db.add_all([first, second, *cameras, Configuration(key="timezone", value="Asia/Makassar")])
        db.flush()
        # Historical seed newer than an old Offline state must win.
        db.add_all(
            [
                CameraStatusChangeLog(
                    camera_id="shared",
                    previous_status="Online",
                    new_status="Offline",
                    changed_at=START - timedelta(days=3),
                ),
                CameraStatusChangeLog(
                    camera_id="shared",
                    previous_status="Offline",
                    new_status="Online",
                    changed_at=START - timedelta(hours=1),
                ),
                CameraStatusChangeLog(
                    camera_id="shared",
                    previous_status="Online",
                    new_status="Offline",
                    changed_at=NOW - timedelta(hours=2),
                ),
                CameraStatusChangeLog(
                    camera_id="other",
                    previous_status="Online",
                    new_status="Offline",
                    changed_at=START - timedelta(hours=1),
                ),
            ]
        )
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def heatmap_client(
    heatmap_db: Session, monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(map_heatmap, "utc_now", lambda: NOW)
    app = FastAPI()
    app.include_router(map_heatmap.router)
    app.dependency_overrides[map_heatmap.get_db] = lambda: heatmap_db
    app.dependency_overrides[map_heatmap.get_current_user] = lambda: SimpleNamespace(group_id=None)
    with TestClient(app) as client:
        yield client


def test_endpoint_aggregates_only_mapped_active_cameras(heatmap_client: TestClient) -> None:
    response = heatmap_client.get("/camera-heatmap?period=24h")
    assert response.status_code == 200
    result = response.json()
    assert result["timezone"] == "Asia/Makassar"
    assert result["start"].endswith("Z")
    assert result["end"].endswith("Z")
    assert result["mapped_cameras"] == 3
    assert result["incomplete_history_cameras"] == 1
    points = {point["camera_id"]: point for point in result["points"]}
    assert set(points) == {"shared", "other"}
    assert points["shared"]["downtime_seconds"] == 7200
    assert points["shared"]["observed_seconds"] == 86400
    assert points["shared"]["intensity"] == pytest.approx(2 / 24)
    assert points["shared"]["lat"] == 0
    assert points["other"]["downtime_seconds"] == 86400


def test_endpoint_applies_many_to_many_group_access(heatmap_client: TestClient) -> None:
    heatmap_client.app.dependency_overrides[map_heatmap.get_current_user] = lambda: SimpleNamespace(
        group_id=101
    )
    result = heatmap_client.get("/camera-heatmap?period=24h").json()
    assert result["mapped_cameras"] == 1
    assert [point["camera_id"] for point in result["points"]] == ["shared"]


@pytest.mark.parametrize("period", ["24h", "7d", "30d"])
def test_valid_periods_and_bounded_weights(heatmap_client: TestClient, period: str) -> None:
    result = heatmap_client.get(f"/camera-heatmap?period={period}").json()
    assert result["period"] == period
    assert all(0 < point["intensity"] <= 1 for point in result["points"])


def test_empty_group_and_invalid_period(heatmap_client: TestClient) -> None:
    heatmap_client.app.dependency_overrides[map_heatmap.get_current_user] = lambda: SimpleNamespace(
        group_id=999
    )
    assert heatmap_client.get("/camera-heatmap").json()["points"] == []
    assert heatmap_client.get("/camera-heatmap?period=365d").status_code == 422


def test_endpoint_requires_authentication(heatmap_client: TestClient) -> None:
    def unauthenticated() -> None:
        raise HTTPException(status_code=401, detail="Authentication required")

    heatmap_client.app.dependency_overrides[map_heatmap.get_current_user] = unauthenticated
    assert heatmap_client.get("/camera-heatmap").status_code == 401
