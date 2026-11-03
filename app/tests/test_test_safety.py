"""Regression checks for collection safety and real PostgreSQL fixture isolation."""

from __future__ import annotations

import socket
import subprocess
import sys

from pathlib import Path
from uuid import uuid4

import pytest

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.database import engine as application_engine
from app.models.record_check import RecordSource


def test_manual_diagnostics_are_import_safe() -> None:
    """Import every relocated script without changing cwd or importing the app."""
    root = Path(__file__).resolve().parents[2]
    script = """
import os, runpy, sys
from pathlib import Path
cwd = os.getcwd()
for path in Path('scripts/diagnostics').glob('*.py'):
    runpy.run_path(str(path), run_name='collection_safety_check')
assert os.getcwd() == cwd
assert not any(name == 'app' or name.startswith('app.') for name in sys.modules)
print('diagnostic imports are inert')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "diagnostic imports are inert"


def test_application_database_is_never_a_test_fallback() -> None:
    """Fail before opening a connection even if an application session escapes."""
    with pytest.raises(AssertionError, match="not the application engine"):
        application_engine.connect()


def test_unmocked_external_connections_are_blocked() -> None:
    """Reject DNS and direct connections before any external transport happens."""
    with pytest.raises(AssertionError, match="External network"):
        socket.getaddrinfo("example.invalid", 443)
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="External network"):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(AssertionError, match="External network"):
            sock.connect_ex(("192.0.2.1", 443))


def test_database_commits_stay_inside_test_transaction(
    db_session: Session, postgres_test_engine: Engine
) -> None:
    """Application commits must not become visible to another connection."""
    source = RecordSource(name=f"test-{uuid4().hex}", base_path="/synthetic")
    db_session.add(source)
    db_session.commit()
    assert db_session.get(RecordSource, source.id) is source
    with postgres_test_engine.connect() as connection:
        assert (
            connection.scalar(select(RecordSource.id).where(RecordSource.id == source.id)) is None
        )


def test_postgres_fixture_preserves_jsonb_and_audit_triggers(postgres_test_engine: Engine) -> None:
    """Shared fixtures use PostgreSQL semantics without disabling append-only DDL."""
    with postgres_test_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert schema.startswith("pytest_")
        columns = {column["name"]: column for column in inspect(connection).get_columns("users")}
        assert str(columns["web_tokens"]["type"]) == "JSONB"
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.triggers "
                    "WHERE event_object_schema = current_schema() "
                    "AND event_object_table = 'audit_logs'"
                )
            )
            == 2
        )
