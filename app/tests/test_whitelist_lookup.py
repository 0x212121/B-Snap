"""Whitelist lookup behavior against a minimal, isolated SQLite schema."""

from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import whatsapp_routes
from app.db.database import Base, get_db
from app.models.camera_group import CameraGroup
from app.models.whitelist import WhatsappWhitelist
from app.routes import wa_webhook, whitelist
from app.routes.auth import admin_access_required


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Do not create the full PostgreSQL-specific schema in SQLite."""


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[CameraGroup.__table__, WhatsappWhitelist.__table__])
    with Session(engine) as db:
        db.add(WhatsappWhitelist(phone_number="+12025550123", name="Alice"))
        db.add(WhatsappWhitelist(phone_number="628123456789", name="Legacy"))
        db.commit()
        monkeypatch.setattr(whitelist, "log_audit", lambda **_kwargs: None)
        app = FastAPI()
        app.include_router(whitelist.router)
        app.include_router(whatsapp_routes.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(username="admin")
        with TestClient(app) as test_client:
            yield test_client
    engine.dispose()


@pytest.mark.parametrize("number", ["12025550123", "+1 (202) 555-0123"])
def test_allowed_accepts_equivalent_formats(client, number):
    response = client.get("/api/whatsapp/is-allowed", params={"phone": number})
    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert response.json()["name"] == "Alice"


@pytest.mark.parametrize("number", ["+012025550123", "+1 202 555 0123 ext 9", "x12025550123"])
def test_allowed_rejects_malformed_identities(client, number):
    assert client.get("/api/whatsapp/is-allowed", params={"phone": number}).json() == {
        "allowed": False
    }


def test_search_and_delete_use_equivalent_number(client):
    response = client.get("/api/whitelist", params={"search": "+1 (202)"})
    assert response.json()["data"][0]["phone_number"] == "+12025550123"
    assert len(client.get("/api/whitelist/group/%2B12025550123").json()["results"]) == 1
    assert client.delete("/api/whitelist/%2B12025550123").status_code == 200
    assert client.delete("/api/whitelist/%2B12025550123").status_code == 404
    assert client.get("/api/whatsapp/is-allowed", params={"phone": "12025550123"}).json() == {
        "allowed": False
    }


def test_old_indonesian_number_remains_accessible(client):
    result = client.get("/api/whatsapp/is-allowed", params={"phone": "+62 812 3456 789"})
    assert result.json()["name"] == "Legacy"


def test_new_number_and_legacy_duplicate(client):
    duplicate = client.post("/api/whitelist", json={"phone_number": "8123456789", "country": "ID"})
    assert duplicate.status_code == 409
    added = client.post("/api/whitelist", json={"phone_number": "020 7946 0018", "country": "GB"})
    assert added.status_code == 200
    response = client.get("/api/whitelist", params={"search": "+442079460018"})
    assert response.json()["data"][0]["phone_number"] == "+442079460018"


def test_database_unique_index_blocks_both_representations():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[CameraGroup.__table__, WhatsappWhitelist.__table__])
    with Session(engine) as db:
        db.add(WhatsappWhitelist(phone_number="628123456789"))
        db.commit()
        db.add(WhatsappWhitelist(phone_number="+628123456789"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.query(WhatsappWhitelist).count() == 1
    engine.dispose()


def test_webhook_resolves_canonical_and_legacy_numbers():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[CameraGroup.__table__, WhatsappWhitelist.__table__])
    with Session(engine) as db:
        db.add_all(
            [
                WhatsappWhitelist(phone_number="+12025550123", name="New"),
                WhatsappWhitelist(phone_number="628123456789", name="Legacy"),
            ]
        )
        db.commit()
        assert wa_webhook._whitelisted_sender(db, "12025550123@s.whatsapp.net").name == "New"
        assert (
            wa_webhook._whitelisted_sender(db, "+62 812 3456 789@s.whatsapp.net").name == "Legacy"
        )
        assert wa_webhook._whitelisted_sender(db, "x12025550123@s.whatsapp.net") is None
    engine.dispose()
