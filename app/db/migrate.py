"""Run database migrations and initial configuration outside application workers."""

# Database imports are deferred until environment/configuration paths are resolved.
# ruff: noqa: PLC0415

from __future__ import annotations

import os

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from alembic import command


def migration_config() -> Config:
    """Resolve Alembic paths independently of the process working directory."""
    root = Path(__file__).resolve().parents[2]
    config = Config(os.getenv("ALEMBIC_INI_PATH", str(root / "alembic.ini")))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def require_current_schema() -> None:
    """Reject startup until migrations and initial configuration have succeeded."""
    import app.models  # noqa: F401

    from app.core.config_initializer import DEFAULT_CONFIG
    from app.db.database import Base, SessionLocal, engine
    from app.models.config import Configuration

    config = migration_config()
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
        expected = set(ScriptDirectory.from_config(config).get_heads())
        missing_tables = set(Base.metadata.tables) - set(inspect(connection).get_table_names())
    if current != expected or missing_tables:
        raise RuntimeError("Database schema is not ready. Run python -m app.db.migrate first.")
    with SessionLocal() as db:
        existing_keys = {key for (key,) in db.query(Configuration.key).all()}
    if set(DEFAULT_CONFIG) - existing_keys:
        raise RuntimeError(
            "Database configuration is not ready. Run python -m app.db.migrate first."
        )


def main() -> None:
    """Upgrade schema, then insert missing defaults; propagate any failure."""
    # Register models for Alembic metadata without importing the web application.
    import app.models  # noqa: F401

    from app.core.config_initializer import seed_config
    from app.db.database import SessionLocal

    command.upgrade(migration_config(), "head")
    with SessionLocal() as db:
        seed_config(db, raise_on_error=True)
    require_current_schema()


if __name__ == "__main__":
    main()
