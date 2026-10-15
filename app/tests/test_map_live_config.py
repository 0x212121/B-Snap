"""Live-view configuration persistence and role-aware map presentation."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.config import Configuration
from app.routes import auth, config, maps
from app.utils.map_live_access import map_live_enabled


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full schema: only the Configuration table is needed."""


@pytest.fixture
def settings_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[Configuration.__table__])
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_setting_defaults_and_saved_off_are_preserved(settings_db: Session) -> None:
    assert map_live_enabled(settings_db)
    settings_db.add(Configuration(key="map_live_enabled", value="0"))
    settings_db.commit()
    assert not map_live_enabled(settings_db)


@pytest.mark.parametrize("enabled", [True, False])
def test_config_save_persists_live_toggle(
    settings_db: Session, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setattr(config, "set_debug_mode", lambda _enabled: None)
    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[config.get_db] = lambda: settings_db
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role="admin")
    data = {
        "healthcheck_interval_minutes": "15",
        "snapshot_concurrent_workers": "1",
        "max_screenshot_per_camera": "20",
        "snapshot_batch_size": "50",
        "snapshot_batch_delay_seconds": "5",
        "watermark_text": "QA",
        "map_title": "QA",
        "timezone": "Asia/Makassar",
    }
    if enabled:
        data["map_live_enabled"] = "1"
    with TestClient(app) as client:
        assert client.post("/config/save", data=data).status_code == 200
    assert settings_db.get(Configuration, "map_live_enabled").value == str(int(enabled))


@pytest.mark.parametrize(
    ("role", "enabled", "expected"),
    [
        ("admin", "1", True),
        ("admin", "0", False),
        ("operator", "1", False),
        ("viewer", "1", False),
    ],
)
def test_maps_only_exposes_live_controls_to_enabled_admins(
    settings_db: Session, monkeypatch: pytest.MonkeyPatch, role: str, enabled: str, expected: bool
) -> None:
    settings_db.add(Configuration(key="map_live_enabled", value=enabled))
    settings_db.commit()
    monkeypatch.setattr(maps, "get_config", lambda _key, default=None: default)
    monkeypatch.setattr(
        maps.templates,
        "TemplateResponse",
        lambda _name, context: JSONResponse({"enabled": context["live_view_enabled"]}),
    )
    app = FastAPI()
    app.include_router(maps.router)
    app.dependency_overrides[maps.get_db] = lambda: settings_db
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role=role)
    with TestClient(app) as client:
        assert client.get("/maps").json() == {"enabled": expected}
