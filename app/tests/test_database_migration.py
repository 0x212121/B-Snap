"""Isolated migration checks; PostgreSQL cases use disposable schemas only."""

from __future__ import annotations

import ast
import os

from collections.abc import Generator
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.core.config_initializer import DEFAULT_CONFIG, seed_config
from app.db import database, migrate


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the global SQLite schema fixture; use mocks or disposable PostgreSQL."""
    return


@pytest.fixture
def migration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[Connection, Config], None, None]:
    """Bind all migration operations to a new schema in an explicit test database."""
    url = os.getenv("BSNAP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BSNAP_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    engine = create_engine(url)
    schema = f"migration_test_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}"'))
        connection.commit()
        config = migrate.migration_config()
        config.attributes["connection"] = connection
        monkeypatch.setattr(migrate, "migration_config", lambda: config)
        readiness_engine = MagicMock()
        readiness_engine.connect.side_effect = lambda: nullcontext(connection)
        monkeypatch.setattr(database, "engine", readiness_engine)
        monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=connection))
        try:
            yield connection, config
        finally:
            connection.rollback()
            connection.execute(text("SET search_path TO public"))
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def test_seed_failure_is_propagated() -> None:
    db = MagicMock()
    db.query.side_effect = SQLAlchemyError("isolated seed failure")
    with pytest.raises(SQLAlchemyError, match="isolated seed failure"):
        seed_config(db, raise_on_error=True)
    db.rollback.assert_called_once()


def test_failed_upgrade_does_not_seed_or_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    upgrade = MagicMock(side_effect=RuntimeError("isolated migration failure"))
    stamp = MagicMock()
    seed = MagicMock()
    monkeypatch.setattr(migrate.command, "upgrade", upgrade)
    monkeypatch.setattr(migrate.command, "stamp", stamp)
    monkeypatch.setattr("app.core.config_initializer.seed_config", seed)
    with pytest.raises(RuntimeError, match="isolated migration failure"):
        migrate.main()
    seed.assert_not_called()
    stamp.assert_not_called()


def test_web_lifespan_does_not_modify_schema() -> None:
    tree = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
    lifespan = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )
    calls = [node for node in ast.walk(lifespan) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"create_all", "upgrade", "stamp"}
        for node in calls
    )
    assert not any(
        isinstance(node.func, ast.Name) and node.func.id == "seed_config" for node in calls
    )


def test_compose_waits_for_successful_migration() -> None:
    yaml = pytest.importorskip("yaml", reason="Compose validation needs the optional PyYAML parser")
    services = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert services["migrate"]["restart"] == "no"
    assert services["migrate"]["command"] == ["/app/start.sh", "migrate"]
    for name in ("b-snap", "scheduler", "notifier"):
        assert services[name]["image"] == services["migrate"]["image"]
        assert services[name]["depends_on"]["postgres"]["condition"] == "service_healthy"
        assert (
            services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        )


def test_empty_database_upgrade_and_repeat_preserve_defaults(
    migration_database: tuple[Connection, Config],
) -> None:
    connection, _ = migration_database
    with pytest.raises(RuntimeError, match="schema is not ready"):
        migrate.require_current_schema()
    migrate.main()
    inspector = inspect(connection)
    for name, table in database.Base.metadata.tables.items():
        assert name in inspector.get_table_names()
        assert set(table.c.keys()) <= {column["name"] for column in inspector.get_columns(name)}
    connection.execute(
        text("UPDATE configurations SET value = '123' WHERE key = 'snapshot_interval_minutes'")
    )
    connection.execute(
        text("DELETE FROM configurations WHERE key = 'record_check_interval_minutes'")
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="configuration is not ready"):
        migrate.require_current_schema()
    migrate.main()
    assert (
        connection.scalar(
            text("SELECT value FROM configurations WHERE key = 'snapshot_interval_minutes'")
        )
        == "123"
    )
    assert (
        connection.scalar(
            text("SELECT value FROM configurations WHERE key = 'record_check_interval_minutes'")
        )
        == DEFAULT_CONFIG["record_check_interval_minutes"]
    )
    assert (
        connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.triggers WHERE event_object_schema = current_schema() AND event_object_table = 'audit_logs'"
            )
        )
        == 2
    )
    assert "audit_logs_unified" in inspect(connection).get_view_names()


def test_existing_revision_upgrades_without_replaying_baseline(
    migration_database: tuple[Connection, Config],
) -> None:
    connection, config = migration_database
    migrate.command.upgrade(config, "20260930_wa_provider_id")
    connection.execute(
        text("INSERT INTO configurations (key, value) VALUES ('snapshot_interval_minutes', '321')")
    )
    connection.execute(
        text("INSERT INTO audit_logs (\"user\", action) VALUES ('test', 'test_upgrade')")
    )
    connection.commit()
    migrate.main()
    assert (
        connection.scalar(
            text("SELECT value FROM configurations WHERE key = 'snapshot_interval_minutes'")
        )
        == "321"
    )
    assert (
        connection.scalar(text("SELECT count(*) FROM audit_logs WHERE action = 'test_upgrade'"))
        == 1
    )
