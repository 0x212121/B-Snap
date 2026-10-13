"""Resolver coverage using only SQLite-compatible tables in an isolated database."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.whitelist import WhatsappWhitelist
from app.routes.auth import admin_access_required
from app.routes.resolve_ip import router


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema fixture; JSONB/audit tables are not needed."""


@pytest.fixture
def resolver_client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            WhatsappWhitelist.__table__,
        ],
    )
    with Session(engine) as session:
        allowed = CameraGroup(name="Resolver allowed")
        denied = CameraGroup(name="Resolver denied")
        session.add_all([allowed, denied])
        session.flush()
        session.add_all(
            [
                WhatsappWhitelist(phone_number="628123456789", group_id=allowed.id),
                Camera(hostname="gate-ip", ip="192.168.1.10", groups=[allowed]),
                Camera(hostname="gate-null", ip=None, groups=[allowed]),
                Camera(hostname="gate-empty", ip="", groups=[allowed]),
                Camera(hostname="gate-denied", ip=None, groups=[denied]),
            ]
        )
        session.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(group_id=None)
        with TestClient(app) as client:
            yield client
    engine.dispose()


@pytest.mark.parametrize("keyword", ["gate", "Resolver allowed"])
@pytest.mark.parametrize("include", [None, "false", "true"])
def test_include_without_ip(resolver_client: TestClient, keyword: str, include: str | None) -> None:
    params = {"keyword": keyword, "phone_number": "628123456789"}
    if include is not None:
        params["include_without_ip"] = include
    response = resolver_client.get("/api/cctv/resolve-ip", params=params)
    assert response.status_code == 200
    expected = {"gate-ip": "192.168.1.10"}
    if include == "true":
        expected.update({"gate-empty": "", "gate-null": None})
    body = response.json()
    assert body["count"] == len(expected)
    assert {item["name"]: item["ip"] for item in body["results"]} == expected
    assert [item["name"] for item in body["results"]] == sorted(expected)


@pytest.mark.parametrize(
    ("keyword", "phone", "include", "status"),
    [
        ("gate-null", "628123456789", "false", 404),
        ("gate-empty", "628123456789", "false", 404),
        ("gate-denied", "628123456789", "true", 403),
        ("Resolver denied", "628123456789", "true", 403),
        ("gate", "", "true", 403),
        ("gate", "628999999999", "true", 403),
        ("missing", "628123456789", "true", 404),
        ("gate", "628123456789", "invalid", 422),
    ],
)
def test_include_without_ip_errors(
    resolver_client: TestClient, keyword: str, phone: str, include: str, status: int
) -> None:
    response = resolver_client.get(
        "/api/cctv/resolve-ip",
        params={"keyword": keyword, "phone_number": phone, "include_without_ip": include},
    )
    assert response.status_code == status
    if keyword == "missing":
        assert response.json()["detail"] == "No camera matches the provided keyword."
