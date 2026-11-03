"""Canonical documentation, preserved bookmarks, and inactive legacy views."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import main
from app.db.database import get_db
from app.routes import docs
from app.routes.auth import get_current_user


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Route/render checks do not start DB schema fixtures or application workers."""


@pytest.fixture
def routing_client(monkeypatch):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.router.routes.extend(main.app.routes)
    app.add_middleware(SessionMiddleware, secret_key="isolated-doc-routing-key")
    user = SimpleNamespace(id=7, role="admin", username="Test admin")
    db = Mock()

    def current_user():
        if user.role is None:
            raise HTTPException(status_code=401)
        return user

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_db] = lambda: db
    # Copied production APIRoutes retain their original dependency provider.
    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, current_user)
    monkeypatch.setitem(main.app.dependency_overrides, get_db, lambda: db)

    def render(name, context, **kwargs):
        values = {
            "user_role": user.role or "viewer",
            "user_name": "Test",
            "nav_items": [],
            "current_path": context["request"].url.path,
            "timezone": "UTC",
            "version": "test",
            "branding": {
                "name": "B-Snap",
                "tagline": "",
                "logo": "/static/icons/b-snap.png",
                "palette": "blue",
            },
            **context,
        }
        return HTMLResponse(
            docs.templates.env.get_template(name).render(**values),
            status_code=kwargs.get("status_code", 200),
        )

    monkeypatch.setattr(docs.templates, "TemplateResponse", render)
    with TestClient(app) as client:
        yield client, user, db


def test_production_method_path_pairs_are_unique():
    registrations = Counter(
        (method, route.path)
        for route in main.app.routes
        for method in getattr(route, "methods", set())
    )
    assert not {key: count for key, count in registrations.items() if count > 1}


def test_documentation_index_redirects_to_one_canonical_page(routing_client):
    client, _user, db = routing_client
    response = client.get("/docs", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs/environment"
    response = client.get("/docs/", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/docs/environment"
    assert "TRUSTED_PROXY_HOSTS" in response.text
    assert "Use specific domains" not in response.text
    db.query.assert_not_called()


def test_swagger_bookmark_and_published_schema_alias_resolve(routing_client):
    client, _user, _db = routing_client
    response = client.get("/developer/docs", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs/openapi"
    response = client.get("/developer/docs", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/docs/openapi"
    assert 'url: "/openapi.json"' in response.text
    assert "SwaggerUIBundle" in response.text
    assert "/static/swagger-ui-bundle.js" not in response.text
    response = client.get("/docs/openapi.json", follow_redirects=True)
    assert response.status_code == 200
    assert response.url.path == "/openapi.json"
    assert response.json()["openapi"].startswith("3.")


@pytest.mark.parametrize("path", ["/docs/openapi", "/developer/docs"])
@pytest.mark.parametrize(
    ("role", "expected"), [(None, 401), ("operator", 403), ("viewer", 403), ("user", 403)]
)
def test_swagger_ui_keeps_admin_access(routing_client, path, role, expected):
    client, user, _db = routing_client
    user.role = role
    assert client.get(path, follow_redirects=False).status_code == expected


def test_legacy_stats_redirect_preserves_encoded_filters_without_rendering_old_view(routing_client):
    client, _user, db = routing_client
    camera = "Gate A&B / 摄像头"
    response = client.get("/stats", params={"camera": camera, "days": 30}, follow_redirects=False)
    assert response.status_code == 301
    destination = urlsplit(response.headers["location"])
    assert destination.path == "/analytics"
    assert parse_qs(destination.query) == {"tab": ["snapshots"], "camera": [camera], "days": ["30"]}
    db.query.assert_not_called()


@pytest.mark.parametrize(
    ("path", "status", "destination"),
    [
        ("/insights", 301, "/analytics?tab=overview"),
        ("/change-password", 302, "/profile"),
        ("/documentation-index", 307, "/documentation/index.html"),
    ],
)
def test_other_existing_bookmarks_are_preserved(routing_client, path, status, destination):
    client, _user, _db = routing_client
    response = client.get(path, follow_redirects=False)
    assert response.status_code == status
    assert response.headers["location"] == destination


def test_unregistered_mfa_router_stays_disabled_and_nested_map_routes_stay_active(routing_client):
    client, _user, _db = routing_client
    assert client.post("/2fa/enable").status_code == 404
    modules = {route.endpoint.__module__ for route in main.app.routes if hasattr(route, "endpoint")}
    assert "app.routes.map_live" in modules
    assert "app.routes.map_heatmap" in modules
    assert "app.routes.dev_docs" not in modules
