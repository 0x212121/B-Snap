"""Map response compatibility and query budgets on isolated compatible tables."""

from __future__ import annotations

import inspect
import threading

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.camera_status_change_log import CameraStatusChangeLog as Log
from app.models.config import Configuration
from app.models.snapshot import Snapshot
from app.routes import maps

NOW = datetime(2026, 10, 22, 16, 2, tzinfo=UTC)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; only these compatible tables are created."""


@pytest.fixture
def map_db(monkeypatch: pytest.MonkeyPatch) -> Generator[tuple[Session, list[str]], None, None]:
    """Keep map writes isolated from application PostgreSQL and freeze the UTC clock."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            Log.__table__,
            Snapshot.__table__,
            Configuration.__table__,
        ],
    )
    queries: list[str] = []

    @event.listens_for(engine, "after_cursor_execute")
    def record_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(maps, "datetime", FixedClock)
    try:
        with Session(engine) as db:
            db.add(Configuration(key="timezone", value="Asia/Makassar"))
            db.commit()
            queries.clear()
            yield db, queries
    finally:
        engine.dispose()


def user(group_id: int | None = None) -> SimpleNamespace:
    """Represent an already authenticated user with its eager-loaded group."""
    return SimpleNamespace(
        group_id=group_id,
        group=SimpleNamespace(name="Group A") if group_id else None,
    )


def camera(db: Session, name: str, status: str = "Active", **values: object) -> None:
    """Insert a minimal camera without loading relationship collections."""
    db.execute(insert(Camera), {"id": name, "hostname": name, "status": status, **values})


def transition(db: Session, name: str, minutes: int, old: str, new: str, *, key: str = "") -> None:
    """Insert a transition at a fixed UTC offset from the test clock."""
    db.execute(
        insert(Log),
        {
            "id": key or f"{name}-{minutes}",
            "camera_id": name,
            "changed_at": NOW + timedelta(minutes=minutes),
            "previous_status": old,
            "new_status": new,
        },
    )


def snapshot(db: Session, name: str, minutes: int, **values: object) -> None:
    """Insert snapshot metadata; media files are not opened by this endpoint."""
    db.execute(
        insert(Snapshot),
        {
            "id": f"{name}-{minutes}",
            "camera_id": name,
            "camera_name": name,
            "camera_ip": "127.0.0.1",
            "file_path": "test.jpg",
            "timestamp": NOW + timedelta(minutes=minutes),
            **values,
        },
    )


def test_status_uptime_fallback_and_payload_are_preserved(
    map_db: tuple[Session, list[str]],
) -> None:
    db, _queries = map_db
    for name, status in [
        ("online", "Active"),
        ("offline", "Active"),
        ("recover", "Active"),
        ("maintenance", "Maintenance"),
        ("standalone", "Standalone"),
        ("standalone-snap", "Standalone"),
        ("no-history", "Active"),
        ("deleted-snap", "Active"),
    ]:
        camera(db, name, status, latitude=0, longitude=117)
    transition(db, "online", -2880, "Offline", "Online")
    transition(db, "online", -1440, "Online", "High Latency")
    transition(db, "online", -60, "High Latency", "Online")
    transition(db, "offline", -180, "Offline", "Online")
    transition(db, "offline", -120, "Online", "Offline")
    transition(db, "offline", -60, "Offline", "Unknown")
    transition(db, "maintenance", -120, "Offline", "Online")
    transition(db, "recover", -60, "Online", "Offline")
    for name in ("recover", "maintenance", "standalone-snap", "deleted-snap"):
        snapshot(db, name, -3, deleted_at=NOW if name == "deleted-snap" else None)
    db.commit()
    rows = {row.id: row for row in maps.get_camera_locations(db, user())}
    assert rows["online"].status == "Online"
    assert rows["online"].uptime == "2d 0h 0m"
    assert rows["online"].last_online == "22/10/2026 - 23:02:00 WITA"
    assert rows["offline"].status == "Unknown"
    assert rows["offline"].uptime == "1h 0m"
    assert rows["offline"].last_online == "22/10/2026 - 21:02:00 WITA"
    for name in ("recover", "standalone-snap", "deleted-snap"):
        assert rows[name].status == "Online"
        assert rows[name].uptime == "3m"
        assert rows[name].last_online == "22/10/2026 - 23:59:00 WITA"
    assert rows["maintenance"].status == "Maintenance"
    assert rows["maintenance"].uptime == "N/A"
    assert rows["standalone"].status == "Standalone"
    assert rows["no-history"].status == "Active"
    assert rows["no-history"].last_online == "Unknown"
    assert rows["online"].lat == 0
    assert set(rows["online"].model_dump()) == {
        "id",
        "hostname",
        "ip",
        "lat",
        "lng",
        "asset_no",
        "status",
        "last_online",
        "uptime",
        "restricted",
        "cam_group",
        "user_group",
        "user_group_id",
        "coordinate",
        "note",
    }


@pytest.mark.parametrize("count", [1, 100])
def test_queries_and_loaded_history_rows_are_bounded(
    map_db: tuple[Session, list[str]],
    count: int,
) -> None:
    db, queries = map_db
    for index in range(count):
        name = f"camera-{index}"
        camera(db, name)
        snapshot(db, name, -30)
        for offset in range(30):
            transition(db, name, -offset - 1, "Offline" if offset == 29 else "Online", "Online")
    db.commit()
    queries.clear()
    result = maps.get_camera_locations(db, user())
    assert len(result) == count
    assert len(queries) == 4
    assert sum("configurations" in statement for statement in queries) == 1
    assert not any(isinstance(item, (Snapshot, Log)) for item in db.identity_map.values())


def test_http_group_access_uses_many_to_many_and_preserves_json(
    map_db: tuple[Session, list[str]],
) -> None:
    db, _queries = map_db
    db.add_all([CameraGroup(id=101, name="Group A"), CameraGroup(id=102, name="Group B")])
    camera(db, "shared", group_id=102)
    camera(db, "other", group_id=101)
    camera(db, "no-group")
    camera(db, "disabled", "Inactive")
    db.execute(
        insert(camera_camera_groups),
        [
            {"camera_id": "shared", "camera_group_id": 101},
            {"camera_id": "shared", "camera_group_id": 102},
            {"camera_id": "other", "camera_group_id": 102},
        ],
    )
    for name in ("shared", "other", "no-group"):
        snapshot(db, name, -1)
        transition(db, name, -30, "Online", "Offline")
    db.commit()
    app = FastAPI()
    app.include_router(maps.router)
    app.dependency_overrides[maps.get_db] = lambda: db
    app.dependency_overrides[maps.get_current_user] = lambda: user(101)
    with TestClient(app) as client:
        response = client.get("/camera-locations")
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == ["shared"]
        assert response.json()[0]["user_group"] == "Group A"
        app.dependency_overrides[maps.get_current_user] = user
        assert {row["id"] for row in client.get("/camera-locations").json()} == {
            "shared",
            "other",
            "no-group",
        }
    assert not inspect.iscoroutinefunction(maps.get_camera_locations)


@pytest.mark.parametrize(
    ("zone", "label"),
    [
        ("Asia/Jakarta", "22/10/2026 - 23:01:00 WIB"),
        ("Asia/Makassar", "23/10/2026 - 00:01:00 WITA"),
        ("Asia/Jayapura", "23/10/2026 - 01:01:00 WIT"),
        ("America/New_York", "22/10/2026 - 12:01:00 EDT"),
    ],
)
def test_timezone_conversion_once_and_duration_stays_utc(
    map_db: tuple[Session, list[str]],
    zone: str,
    label: str,
) -> None:
    db, _queries = map_db
    db.get(Configuration, "timezone").value = zone
    camera(db, "camera")
    transition(db, "camera", -1, "Offline", "Online")
    db.commit()
    row = maps.get_camera_locations(db, user())[0]
    assert row.last_online == label
    assert row.uptime == "1m"


def test_empty_group_skips_history_queries(map_db: tuple[Session, list[str]]) -> None:
    db, queries = map_db
    queries.clear()
    assert maps.get_camera_locations(db, user(99)) == []
    assert len(queries) == 1


def test_equal_transition_times_have_stable_status(map_db: tuple[Session, list[str]]) -> None:
    db, _queries = map_db
    camera(db, "camera")
    transition(db, "camera", -10, "Offline", "Online", key="a")
    transition(db, "camera", -10, "Online", "Offline", key="z")
    db.commit()
    assert maps.get_camera_locations(db, user())[0].status == "Offline"


def test_synchronous_queries_run_concurrently_off_event_loop(
    map_db: tuple[Session, list[str]],
) -> None:
    """Two blocked SQL queries can run together through the actual FastAPI route."""
    db, _queries = map_db
    camera(db, "camera")
    db.commit()
    engine = db.get_bind()
    barrier = threading.Barrier(2)
    threads: set[int] = set()

    def pause_camera_query(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("SELECT cameras."):
            threads.add(threading.get_ident())
            barrier.wait(timeout=3)

    def sessions() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(maps.router)
    app.dependency_overrides[maps.get_db] = sessions
    app.dependency_overrides[maps.get_current_user] = user
    event.listen(engine, "before_cursor_execute", pause_camera_query)
    try:
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(lambda _index: client.get("/camera-locations"), range(2)))
        assert [response.status_code for response in responses] == [200, 200]
        assert len(threads) == 2
    finally:
        event.remove(engine, "before_cursor_execute", pause_camera_query)
