"""Retention and analytics checks against isolated API-log tables."""

from __future__ import annotations

import os
import runpy
import uuid

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.api_daily_stats import ApiDailyStats
from app.models.config import Configuration
from app.models.log import ApiLog
from app.routes.insights import _daily_counts, _total_count
from app.utils.api_daily_stats import api_daily_counts, archive_expired_api_logs


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared full-schema SQLite fixture; use only compatible tables."""


@pytest.fixture(params=["sqlite", "postgresql"])
def api_db(request: pytest.FixtureRequest):
    """Use SQLite or an explicitly supplied PostgreSQL test database in a fresh schema."""
    schema = "test_api_stats_" + uuid.uuid4().hex
    admin_engine = None
    if request.param == "postgresql":
        url = os.environ.get("BSNAP_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set BSNAP_TEST_POSTGRES_URL to an isolated PostgreSQL test database")
        admin_engine = create_engine(url)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine("sqlite:///:memory:")
    tables = [ApiLog.__table__, ApiDailyStats.__table__, Configuration.__table__]
    Base.metadata.create_all(engine, tables=tables)
    try:
        with Session(engine) as db:
            db.add(Configuration(key="timezone", value="Asia/Makassar"))
            db.commit()
            yield db
    finally:
        engine.dispose()
        if admin_engine:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()


def _log(db: Session, timestamp: str, status: int = 200) -> None:
    db.add(
        ApiLog(
            timestamp=datetime.fromisoformat(timestamp), endpoint="/api/test", status_code=status
        )
    )


def _range() -> tuple[datetime, datetime]:
    return datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)


def test_counts_survive_cleanup_and_retries(api_db: Session) -> None:
    _log(api_db, "2026-01-02T15:59:00+00:00")
    _log(api_db, "2026-01-02T16:00:00+00:00", 500)
    _log(api_db, "2026-01-10T01:00:00+00:00")
    api_db.commit()
    start, end = _range()
    before = _daily_counts(api_db, ApiLog, "timestamp", start, end)
    assert before == [
        {"date": "2026-01-02", "count": 1},
        {"date": "2026-01-03", "count": 1},
        {"date": "2026-01-10", "count": 1},
    ]
    assert archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC)) == 2
    api_db.commit()
    assert _daily_counts(api_db, ApiLog, "timestamp", start, end) == before
    assert _total_count(api_db, ApiLog, "timestamp", start, end) == 3
    assert api_db.query(ApiLog).count() == 1
    assert archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC)) == 0
    api_db.commit()
    assert api_daily_counts(api_db, start, end) == before
    # A late log for an archived date increments the existing bucket only once.
    _log(api_db, "2026-01-02T16:30:00+00:00")
    api_db.commit()
    assert archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC)) == 1
    api_db.commit()
    assert api_daily_counts(api_db, start, end)[1] == {"date": "2026-01-03", "count": 2}


def test_archive_and_deletion_roll_back_together(api_db: Session) -> None:
    _log(api_db, "2026-01-02T00:00:00+00:00")
    api_db.commit()
    assert archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC)) == 1
    api_db.rollback()
    assert api_db.query(ApiLog).count() == 1
    assert api_db.query(ApiDailyStats).count() == 0


@pytest.mark.parametrize(
    "zone", ["Asia/Jakarta", "Asia/Makassar", "Asia/Jayapura", "America/New_York"]
)
def test_cutoff_preserves_complete_local_days(api_db: Session, zone: str) -> None:
    api_db.get(Configuration, "timezone").value = zone
    local_midnight = datetime(2026, 1, 9, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    _log(api_db, local_midnight.isoformat())
    _log(api_db, (local_midnight - timedelta(seconds=1)).isoformat())
    api_db.commit()
    assert archive_expired_api_logs(api_db, local_midnight + timedelta(hours=12)) == 1
    api_db.commit()
    assert api_db.query(ApiLog).count() == 1


def test_timezone_change_keeps_archived_date(api_db: Session) -> None:
    _log(api_db, "2026-01-02T16:00:00+00:00")
    api_db.commit()
    archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC))
    api_db.commit()
    api_db.get(Configuration, "timezone").value = "Asia/Jakarta"
    _log(api_db, "2026-01-02T16:30:00+00:00")
    api_db.commit()
    assert api_daily_counts(api_db, *_range()) == [
        {"date": "2026-01-02", "count": 1},
        {"date": "2026-01-03", "count": 1},
    ]
    assert api_db.query(ApiDailyStats).one().timezone == "Asia/Makassar"


def test_date_range_includes_only_requested_archived_days(api_db: Session) -> None:
    _log(api_db, "2026-01-02T16:00:00+00:00")
    _log(api_db, "2026-01-03T16:00:00+00:00")
    api_db.commit()
    archive_expired_api_logs(api_db, datetime(2026, 1, 9, tzinfo=UTC))
    api_db.commit()
    start = datetime(2026, 1, 2, 16, tzinfo=UTC)
    end = datetime(2026, 1, 3, 15, 59, 59, 999999, tzinfo=UTC)
    assert api_daily_counts(api_db, start, end) == [{"date": "2026-01-03", "count": 1}]


def test_concurrent_postgres_cleanup_does_not_double_count(api_db: Session) -> None:
    if api_db.get_bind().dialect.name != "postgresql":
        pytest.skip("DELETE RETURNING concurrency is tested on PostgreSQL")
    for _ in range(20):
        _log(api_db, "2026-01-02T00:00:00+00:00")
    api_db.commit()
    engine = api_db.get_bind()

    def cleanup() -> int:
        with Session(engine) as db:
            deleted = archive_expired_api_logs(db, datetime(2026, 1, 9, tzinfo=UTC))
            db.commit()
            return deleted

    with ThreadPoolExecutor(max_workers=2) as executor:
        deleted = list(executor.map(lambda _: cleanup(), range(2)))
    assert sum(deleted) == 20
    assert api_db.query(ApiLog).count() == 0
    assert api_db.query(ApiDailyStats).one().request_count == 20


def test_migration_upgrade_and_downgrade(api_db: Session) -> None:
    migration = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "alembic/versions/20261007_api_daily_stats.py")
    )
    ApiDailyStats.__table__.drop(api_db.connection())
    migration["upgrade"].__globals__["op"] = Operations(
        MigrationContext.configure(api_db.connection())
    )
    migration["upgrade"]()
    assert inspect(api_db.connection()).has_table("api_daily_stats")
    migration["downgrade"]()
    assert not inspect(api_db.connection()).has_table("api_daily_stats")
