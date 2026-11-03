"""Map identity stays separate from application branding and its asset uploads."""

from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.branding_assets import map_logo_url
from app.models.config import Configuration
from app.routes import auth, maps


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Only Configuration is needed; do not create the full application schema."""


def test_map_assets_never_fall_back_to_application_logo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "static/branding"
    directory.mkdir(parents=True)
    (directory / "logo.png").write_bytes(b"application")
    assert map_logo_url({"branding_logo": "/static/branding/logo.png"}) is None
    assert map_logo_url({"map_logo": "/static/branding/logo.png"}) is None
    (directory / "map-logo.ico").write_bytes(b"map")
    assert (
        map_logo_url({"map_logo": "/static/branding/map-logo.ico"})
        == "/static/branding/map-logo.ico"
    )
    assert map_logo_url({"map_logo": "https://example.com/logo.png"}) is None
    legacy = tmp_path / "static/icons"
    legacy.mkdir()
    (legacy / "logo.png").write_bytes(b"legacy map")
    assert map_logo_url({}) == "/static/icons/logo.png"
    assert map_logo_url({"map_logo": "/static/branding/map-logo.png"}) == "/static/icons/logo.png"


@pytest.mark.parametrize("app_name", ["B-Snap", "Custom company"])
def test_map_route_passes_map_asset_independently(tmp_path, monkeypatch, app_name):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / "static/branding"
    directory.mkdir(parents=True)
    (directory / "map-logo.png").write_bytes(b"map")
    (directory / "logo.png").write_bytes(b"app")
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Configuration.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(
            [
                Configuration(key="map_logo", value="/static/branding/map-logo.png"),
                Configuration(key="branding_logo", value="/static/branding/logo.png"),
                Configuration(key="branding_name", value=app_name),
            ]
        )
        db.commit()
        monkeypatch.setattr(maps, "get_config", lambda _key, default=None: default)
        monkeypatch.setattr(maps, "get_current_timezone", lambda _db: "Asia/Makassar")
        monkeypatch.setattr(
            maps.templates,
            "TemplateResponse",
            lambda _name, context: JSONResponse({"logo": context["map_logo_url"]}),
        )
        app = FastAPI()
        app.include_router(maps.router)
        app.dependency_overrides[maps.get_db] = lambda: db
        app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role="viewer")
        with TestClient(app) as client:
            assert client.get("/maps").json() == {"logo": "/static/branding/map-logo.png"}
    engine.dispose()


def test_map_title_uses_map_logo_while_navigation_uses_app_branding():
    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"]))
    html = env.get_template("maps.html").render(
        branding={"name": "Application", "logo": "/static/branding/logo.png"},
        map_logo_url="/static/branding/map-logo.ico",
        map_title="Site map",
        request=SimpleNamespace(session={}, url=SimpleNamespace(path="/maps")),
        nav_items=[],
        timezone="UTC",
        refresh_time="12:00",
        live_view_enabled=False,
    )
    assert 'const MAP_LOGO = "/static/branding/map-logo.ico";' in html
    assert 'src="/static/branding/logo.png" class="h-8"' in html
    assert "MAP_BRAND_NAME" not in html
