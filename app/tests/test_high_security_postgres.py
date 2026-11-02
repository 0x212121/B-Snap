"""Security migration and concurrent limits on disposable PostgreSQL schemas."""

from __future__ import annotations

import os

from concurrent.futures import ThreadPoolExecutor

import pytest

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alembic import command
from app.models.auth_rate_limit import AuthRateLimit
from app.tests import test_database_migration as migration_tests
from app.utils.auth_rate_limit import enforce_auth_rate_limit
from app.utils.health_progress import health_progress

# Reuse the explicit PostgreSQL fixture, including its unique-schema cleanup.
migration_database = migration_tests.migration_database


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Only the explicit PostgreSQL fixture creates/drops unique test schemas."""


def test_security_migration_preserves_global_tracker_and_round_trips(migration_database):
    connection, config = migration_database
    command.upgrade(config, "20261012_email_retry_expiry")
    connection.execute(
        text("INSERT INTO health_check_status (id, is_running, total_cameras) VALUES (1, false, 7)")
    )
    connection.commit()
    command.upgrade(config, "head")
    assert "auth_rate_limits" in inspect(connection).get_table_names()
    assert "group_id" in {
        column["name"] for column in inspect(connection).get_columns("health_check_status")
    }
    group_id = connection.execute(
        text("INSERT INTO camera_groups (name) VALUES ('Security scope') RETURNING id")
    ).scalar_one()
    connection.execute(
        text("INSERT INTO health_check_status (id, group_id) VALUES (:id, :group)"),
        {"id": -group_id, "group": group_id},
    )
    connection.commit()
    command.downgrade(config, "20261012_email_retry_expiry")
    assert "auth_rate_limits" not in inspect(connection).get_table_names()
    assert (
        connection.execute(
            text("SELECT total_cameras FROM health_check_status WHERE id=1")
        ).scalar_one()
        == 7
    )
    assert connection.execute(text("SELECT count(*) FROM health_check_status")).scalar_one() == 1
    connection.commit()
    command.upgrade(config, "head")


def test_concurrent_attempt_limits_are_atomic_across_database_sessions(migration_database):
    connection, config = migration_database
    command.upgrade(config, "head")
    schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    connection.commit()
    engine = create_engine(
        os.environ["BSNAP_TEST_DATABASE_URL"],
        execution_options={"schema_translate_map": {None: schema}},
    )

    def attempt(_index):
        request = Request({"type": "http", "headers": [], "client": ("192.0.2.9", 1)})
        with Session(engine) as db:
            try:
                enforce_auth_rate_limit(db, request, "password", "concurrent-identity")
            except HTTPException as exc:
                return exc.status_code
        return 200

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(20)))
        assert results.count(200) == 10
        assert results.count(429) == 10
        with Session(engine) as db:
            assert {row.attempts for row in db.query(AuthRateLimit)} == {20}
    finally:
        engine.dispose()


def test_concurrent_health_triggers_share_only_their_scope_and_preserve_global_id(
    migration_database,
):
    connection, config = migration_database
    command.upgrade(config, "head")
    group_id = connection.execute(
        text("INSERT INTO camera_groups (name) VALUES ('Concurrent health') RETURNING id")
    ).scalar_one()
    schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    connection.commit()
    engine = create_engine(
        os.environ["BSNAP_TEST_DATABASE_URL"],
        execution_options={"schema_translate_map": {None: schema}},
    )

    def start(_index):
        with Session(engine) as db:
            tracker = health_progress(db, group_id, create=True)
            accepted = not tracker.is_running
            tracker.is_running = True
            db.commit()
            return accepted

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(start, range(20)))
        assert results.count(True) == 1
        with Session(engine) as db:
            global_tracker = health_progress(db, None, create=True)
            assert global_tracker.id == 1
            assert not global_tracker.is_running
            assert health_progress(db, group_id).id == -group_id
            db.commit()
    finally:
        engine.dispose()
