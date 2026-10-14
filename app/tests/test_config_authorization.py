"""Configuration authorization checks with mocked database and email transports."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.routes import auth, config


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema setup because all database access is mocked."""


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/config"),
        ("GET", "/admin/config"),
        ("POST", "/config/save"),
        ("POST", "/config/test-smtp"),
        ("POST", "/config/run-cleanup"),
    ],
)
@pytest.mark.parametrize("role", [None, "viewer", "operator"])
def test_config_routes_reject_unauthorized_users(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, role: str | None
) -> None:
    """Run the real admin dependency and reject requests before side effects."""
    db = Mock()
    send_email = Mock()
    monkeypatch.setattr(config, "send_email", send_email)

    def current_user() -> SimpleNamespace:
        if role is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return SimpleNamespace(role=role)

    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[config.get_db] = lambda: db
    app.dependency_overrides[auth.get_current_user] = current_user
    with TestClient(app) as client:
        response = client.request(method, path)

    assert response.status_code == (401 if role is None else 403)
    db.query.assert_not_called()
    db.commit.assert_not_called()
    send_email.assert_not_called()


def test_admin_can_test_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    """An administrator can still submit the SMTP test form and send one email."""
    send_email = Mock()
    monkeypatch.setattr(config, "send_email", send_email)
    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(role="admin")
    with TestClient(app) as client:
        response = client.post("/config/test-smtp", data={"recipient_email": "admin@example.com"})

    assert response.status_code == 200
    send_email.assert_called_once()
    assert send_email.call_args.args[0] == ["admin@example.com"]
