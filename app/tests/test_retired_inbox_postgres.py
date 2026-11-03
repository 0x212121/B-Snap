"""Preserve legacy inbox rows, email history and transport queue through retirement."""

from __future__ import annotations

import pytest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, text

from alembic import command, context as alembic_context
from app.tests import test_database_migration as migration_tests

migration_database = migration_tests.migration_database


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Only the explicit PostgreSQL fixture creates and drops disposable schemas."""


def test_retirement_preserves_history_and_round_trips(migration_database):
    connection, config = migration_database
    command.upgrade(config, "20261102_high_security")
    connection.execute(
        text(
            "INSERT INTO notifications (message, user_id) VALUES ('synthetic historical inbox', NULL)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO camera_email_notification_logs "
            "(camera_name, incident_started_at, sent_at, success) "
            "VALUES ('synthetic camera', now(), now(), true)"
        )
    )
    connection.execute(
        text('INSERT INTO notification_queue (payload) VALUES (\'{"type":"toast"}\')')
    )
    connection.commit()

    def verify_related_tables():
        assert connection.scalar(text("SELECT count(*) FROM camera_email_notification_logs")) == 1
        assert connection.scalar(text("SELECT count(*) FROM notification_queue")) == 1
        assert "email_retry_queue" in inspect(connection).get_table_names()

    command.upgrade(config, "head")
    assert "notifications" not in inspect(connection).get_table_names()
    assert (
        connection.scalar(text("SELECT message FROM retired_notifications"))
        == "synthetic historical inbox"
    )
    verify_related_tables()
    connection.commit()
    command.downgrade(config, "20261102_high_security")
    assert "retired_notifications" not in inspect(connection).get_table_names()
    assert (
        connection.scalar(text("SELECT message FROM notifications")) == "synthetic historical inbox"
    )
    verify_related_tables()
    connection.commit()
    command.upgrade(config, "head")
    assert "notifications" not in inspect(connection).get_table_names()
    assert connection.scalar(text("SELECT count(*) FROM retired_notifications")) == 1
    verify_related_tables()


def test_autogenerate_does_not_drop_archived_rows(migration_database, monkeypatch):
    connection, config = migration_database
    original_configure = alembic_context.configure
    configured = {}

    def capture_configuration(*args, **kwargs):
        configured.update(kwargs)
        return original_configure(*args, **kwargs)

    monkeypatch.setattr(alembic_context, "configure", capture_configuration)
    command.upgrade(config, "head")
    # Compare an empty runtime model against the actual retired table. Without
    # env.py's exclusion, Alembic would propose dropping that historical data.
    context = MigrationContext.configure(
        connection,
        opts={
            "include_object": configured["include_object"],
            "include_name": lambda name, type_, _parents: (
                type_ != "table" or name == "retired_notifications"
            ),
        },
    )
    assert compare_metadata(context, MetaData()) == []
