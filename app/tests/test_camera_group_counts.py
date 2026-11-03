"""Stable group counts and a constant SQL budget on isolated compatible tables."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, delete, event, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.nvr import NVR
from app.routes import camera_groups
from app.routes.auth import get_current_user


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the full SQLite fixture; only compatible count columns are needed."""


def seed_counts(size):
    """Build exact expected counts from synthetic membership sets."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[CameraGroup.__table__, Camera.__table__, NVR.__table__, camera_camera_groups],
    )
    # User has PostgreSQL JSONB. This endpoint only selects id/group_id, so the
    # local fixture deliberately creates those columns rather than a full User.
    users = Table(
        "users", MetaData(), Column("id", Integer, primary_key=True), Column("group_id", Integer)
    )
    users.create(engine)
    expected = {
        i: {
            "id": i,
            "name": f"Group {size-i:04d}",
            "camera_count": 0,
            "nvr_count": 0,
            "user_count": 0,
            "total_usage": 0,
        }
        for i in range(1, size + 1)
    }
    with engine.begin() as connection:
        connection.execute(delete(CameraGroup))
        if size:
            connection.execute(
                insert(CameraGroup), [{"id": i, "name": row["name"]} for i, row in expected.items()]
            )
        active = max(0, size - 1)
        for group in range(1, active + 1):
            for index in range(3):
                camera_id = f"camera-{group}-{index}"
                # A stale legacy scalar must not count the camera in the empty group.
                connection.execute(
                    insert(Camera), {"id": camera_id, "hostname": camera_id, "group_id": size}
                )
                memberships = {group}
                if index == 0 and active > 1:
                    memberships.add(group % active + 1)
                for member in memberships:
                    connection.execute(
                        insert(camera_camera_groups),
                        {"camera_id": camera_id, "camera_group_id": member},
                    )
                    expected[member]["camera_count"] += 1
            for index in range(2):
                connection.execute(
                    insert(NVR),
                    {
                        "id": f"nvr-{group}-{index}",
                        "hostname": f"nvr-{group}-{index}",
                        "ip": "192.0.2.1",
                        "group_id": group,
                    },
                )
                expected[group]["nvr_count"] += 1
            for index in range(4):
                connection.execute(insert(users), {"id": group * 10 + index, "group_id": group})
                expected[group]["user_count"] += 1
        connection.execute(insert(users), {"id": 100000, "group_id": None})
        if size:
            # SQLite's disabled FK enforcement lets us represent an orphaned
            # legacy association: only existing Camera rows should be counted.
            connection.execute(
                insert(camera_camera_groups), {"camera_id": "missing-camera", "camera_group_id": 1}
            )
    return engine, expected


@pytest.mark.parametrize("size", [0, 1, 10, 100])
def test_counts_match_memberships_with_one_select_for_any_group_count(size):
    engine, expected = seed_counts(size)
    queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_select(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    with Session(engine) as db:
        app = FastAPI()
        app.include_router(camera_groups.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=7, role="admin", group_id=None
        )
        with TestClient(app) as client:
            response = client.get("/api/camera-groups")
        assert response.status_code == 200
        for row in expected.values():
            row["total_usage"] = row["camera_count"] + row["nvr_count"] + row["user_count"]
        assert response.json() == sorted(expected.values(), key=lambda row: row["name"])
        assert len(queries) == 1
    engine.dispose()


@pytest.mark.parametrize("role", ["operator", "viewer", "user"])
def test_group_count_endpoint_remains_admin_only(role):
    app = FastAPI()
    app.include_router(camera_groups.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=7, role=role, group_id=None
    )
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as client:
        assert client.get("/api/camera-groups").status_code == 403
