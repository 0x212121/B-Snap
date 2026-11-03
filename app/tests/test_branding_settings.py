"""Branding settings workflow with isolated persistence, assets and audit transport."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import FileSystemLoader
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.core.branding import read_branding
from app.models.config import Configuration
from app.routes import config


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Only Configuration is needed; do not initialize the full SQLite schema."""


@pytest.fixture
def settings(tmp_path, monkeypatch):
    template_root = Path(__file__).resolve().parents[2] / "templates"
    monkeypatch.setattr(config.templates.env, "loader", FileSystemLoader(str(template_root)))
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "static/branding"
    directory.mkdir(parents=True)
    (directory / "logo.png").write_bytes(b"existing application image")
    (directory / "map-logo.png").write_bytes(b"existing map image")
    audit = Mock(side_effect=lambda **kwargs: kwargs["db"].commit())
    monkeypatch.setattr(config, "log_audit", audit)
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Configuration.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(
            Configuration(key=key, value=value)
            for key, value in {
                "branding_name": "Old name",
                "branding_logo": "/static/branding/logo.png",
                "map_logo": "/static/branding/map-logo.png",
                "timezone": "Asia/Makassar",
            }.items()
        )
        db.commit()
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="branding-test-only")
        app.include_router(config.router)
        app.dependency_overrides[config.get_db] = lambda: db
        app.dependency_overrides[config.admin_access_required] = lambda: SimpleNamespace(
            username="admin"
        )
        with TestClient(app) as client:
            yield client, db, directory, audit
    engine.dispose()


def image_bytes(format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format=format)
    return stream.getvalue()


def test_upload_and_identity_save_together_without_changing_other_settings(settings):
    client, db, directory, audit = settings
    contents = image_bytes()
    response = client.post(
        "/admin/branding",
        data={"name": " New identity ", "tagline": "Welcome", "palette": "teal"},
        files={"application_logo": ("new.png", contents, "image/png")},
    )
    assert response.status_code == 200
    saved = response.json()["branding"]
    assert saved["name"] == "New identity"
    assert saved["palette"] == "teal"
    assert saved["logo"].startswith("/static/branding/application-")
    assert Path(saved["logo"].lstrip("/")).read_bytes() == contents
    assert read_branding(db) == saved
    assert (directory / "logo.png").read_bytes() == b"existing application image"
    assert (directory / "map-logo.png").read_bytes() == b"existing map image"
    assert db.get(Configuration, "map_logo").value == "/static/branding/map-logo.png"
    assert db.get(Configuration, "timezone").value == "Asia/Makassar"
    audit.assert_called_once()
    assert audit.call_args.kwargs["action"] == "update_branding"


@pytest.mark.parametrize(
    "upload", [b"not an image", b"x" * (512 * 1024 + 1)], ids=["corrupt", "oversized"]
)
def test_invalid_upload_does_not_save_draft_or_change_active_assets(settings, upload):
    client, db, directory, audit = settings
    response = client.post(
        "/admin/branding",
        data={"name": "Changed"},
        files={"application_logo": ("bad.png", upload, "image/png")},
    )
    assert response.status_code == 400
    assert db.get(Configuration, "branding_name").value == "Old name"
    assert (directory / "logo.png").read_bytes() == b"existing application image"
    assert not list(directory.glob("application-*"))
    audit.assert_not_called()


def test_invalid_identity_is_checked_before_writing_uploaded_image(settings):
    client, db, directory, _ = settings
    response = client.post(
        "/admin/branding",
        data={"name": "  "},
        files={"application_logo": ("new.png", image_bytes(), "image/png")},
    )
    assert response.status_code == 400
    assert not list(directory.glob("application-*"))
    assert db.get(Configuration, "branding_name").value == "Old name"


def test_settings_offer_only_available_logos(settings, monkeypatch):
    client, _db, _directory, _ = settings
    captured = {}
    original = config.templates.TemplateResponse

    def render(name, context, *args, **kwargs):
        captured.update(context)
        return original(name, context, *args, **kwargs)

    monkeypatch.setattr(config.templates, "TemplateResponse", render)
    response = client.get("/admin/branding")
    assert response.status_code == 200
    choices = {entry["value"] for entry in captured["logo_choices"]}
    assert choices == {"/static/icons/b-snap.png", "/static/branding/logo.png"}
    assert 'id="branding-preview"' in response.text


def test_existing_urlencoded_client_can_save_and_revert_to_default(settings):
    client, db, _directory, _ = settings
    response = client.post(
        "/admin/branding",
        data={"name": "Default identity", "logo": "/static/icons/b-snap.png", "palette": "orange"},
    )
    assert response.status_code == 200
    assert read_branding(db)["logo"] == "/static/icons/b-snap.png"


def test_failed_database_save_does_not_replace_current_image(settings, monkeypatch):
    client, db, directory, _ = settings
    monkeypatch.setattr(db, "commit", Mock(side_effect=RuntimeError("simulated commit failure")))
    with pytest.raises(RuntimeError, match="simulated commit failure"):
        client.post(
            "/admin/branding",
            data={"name": "New"},
            files={"application_logo": ("new.png", image_bytes(), "image/png")},
        )
    assert (directory / "logo.png").read_bytes() == b"existing application image"
    db.rollback()
    assert db.get(Configuration, "branding_name").value == "Old name"
