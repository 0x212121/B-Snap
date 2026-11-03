"""Exercise the phone identity index migration without an application database."""

import importlib.util

from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema fixture."""


@pytest.fixture
def revision(monkeypatch, tmp_path):
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/20261103_whatsapp_phone_unique.py"
    )
    spec = importlib.util.spec_from_file_location("test_phone_index_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE whatsapp_whitelist (id INTEGER PRIMARY KEY, phone_number TEXT NOT NULL)"
            )
        )
    with engine.connect() as connection:
        monkeypatch.setattr(module, "op", Operations(MigrationContext.configure(connection)))
        yield module, connection
    engine.dispose()


def test_upgrade_keeps_old_values_and_downgrade_removes_index(revision):
    module, connection = revision
    connection.execute(sa.text("INSERT INTO whatsapp_whitelist VALUES (1, '628123456789')"))
    module.upgrade()
    assert (
        connection.execute(sa.text("SELECT phone_number FROM whatsapp_whitelist")).scalar_one()
        == "628123456789"
    )
    with pytest.raises(IntegrityError):
        connection.execute(sa.text("INSERT INTO whatsapp_whitelist VALUES (2, '+628123456789')"))
    module.downgrade()
    connection.execute(sa.text("INSERT INTO whatsapp_whitelist VALUES (2, '+628123456789')"))


def test_upgrade_rejects_existing_conflicts_without_rewriting(revision):
    module, connection = revision
    connection.execute(
        sa.text("INSERT INTO whatsapp_whitelist VALUES (1, '628123456789'), (2, '+628123456789')")
    )
    with pytest.raises(IntegrityError):
        module.upgrade()
    assert connection.execute(sa.text("SELECT COUNT(*) FROM whatsapp_whitelist")).scalar_one() == 2
