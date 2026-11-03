"""Check independent map/application uploads with isolated storage and configuration."""

from io import BytesIO
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.config import Configuration
from app.routes import config


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema fixture; only Configuration is required."""


@pytest.mark.parametrize(("actual_format", "expected_status"), [("PNG", 200), ("JPEG", 400)])
@pytest.mark.parametrize(
    ("field", "destination"),
    [("map_logo", "map-logo.png"), ("app_logo", "map-logo.png"), ("application_logo", "logo.png")],
)
def test_logo_upload_uses_independent_locations(
    *, tmp_path, monkeypatch, actual_format, expected_status, field, destination
):
    monkeypatch.chdir(tmp_path)
    other = "logo.png" if destination == "map-logo.png" else "map-logo.png"
    other_path = tmp_path / "static/branding" / other
    other_path.parent.mkdir(parents=True)
    other_path.write_bytes(b"existing independent logo")
    legacy = tmp_path / "static/icons/logo.png"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"existing legacy map logo")
    monkeypatch.setattr(config, "set_debug_mode", lambda _value: None)
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Configuration.__table__.create(engine)
    app = FastAPI()
    app.include_router(config.router)
    with Session(engine) as db:
        db.add_all(
            [
                Configuration(key="branding_logo", value="/static/branding/logo.png"),
                Configuration(key="map_logo", value="/static/branding/map-logo.ico"),
            ]
        )
        db.commit()
        app.dependency_overrides[config.get_db] = lambda: db
        app.dependency_overrides[config.admin_access_required] = lambda: SimpleNamespace(
            username="admin"
        )
        image = BytesIO()
        Image.new("RGB", (16, 16), "red").save(image, format=actual_format)
        contents = image.getvalue()
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
        with TestClient(app) as client:
            response = client.post(
                "/config/save", data=data, files={field: ("logo.png", contents, "image/png")}
            )
        assert response.status_code == expected_status
        path = tmp_path / "static/branding" / destination
        if expected_status == 200:
            assert path.read_bytes() == contents
            setting = db.get(Configuration, "map_logo")
            assert setting.value == (
                "/static/branding/map-logo.png"
                if field != "application_logo"
                else "/static/branding/map-logo.ico"
            )
        else:
            assert not path.exists()
            assert db.get(Configuration, "map_logo").value == "/static/branding/map-logo.ico"
        assert db.get(Configuration, "branding_logo").value == "/static/branding/logo.png"
        assert legacy.read_bytes() == b"existing legacy map logo"
        assert other_path.read_bytes() == b"existing independent logo"
    engine.dispose()
