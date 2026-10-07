"""Combine archived daily API totals with retained request logs."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text, union_all
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models.api_daily_stats import ApiDailyStats
from app.models.log import ApiLog
from app.utils.timezone_helper import get_current_timezone


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def archive_expired_api_logs(db: Session, cutoff: datetime) -> int:
    """Move expired complete local days into totals without committing.

    The caller commits both the archive and deletion, or rolls both back.
    PostgreSQL groups DELETE RETURNING rows in the database so concurrent
    cleanup workers cannot count the same log twice. Late logs add to the
    existing bucket when they are eventually archived.
    """
    tz_name = get_current_timezone(db)
    tz = ZoneInfo(tz_name)
    cutoff = _utc(cutoff).astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = cutoff.astimezone(UTC)
    if db.get_bind().dialect.name == "postgresql":
        return int(
            db.execute(
                text("""
                    WITH expired AS (
                        DELETE FROM api_logs WHERE timestamp < :cutoff
                        RETURNING timestamp
                    ), archived AS (
                        INSERT INTO api_daily_stats (date, timezone, request_count)
                        SELECT CAST(timezone(:tz, timestamp) AS date), :tz, COUNT(*)
                        FROM expired
                        GROUP BY CAST(timezone(:tz, timestamp) AS date)
                        ORDER BY CAST(timezone(:tz, timestamp) AS date)
                        ON CONFLICT (date, timezone) DO UPDATE
                        SET request_count = api_daily_stats.request_count + EXCLUDED.request_count
                        RETURNING date
                    )
                    SELECT COUNT(*) FROM expired
                """),
                {"cutoff": cutoff, "tz": tz_name},
            ).scalar_one()
        )
    if db.get_bind().dialect.name != "sqlite":
        raise ValueError("API log archival requires PostgreSQL (SQLite is supported for tests)")

    # Isolated SQLite tests only; the application uses PostgreSQL in production.
    timestamps = (
        db.execute(
            delete(ApiLog).where(ApiLog.timestamp < cutoff).returning(ApiLog.timestamp),
            execution_options={"synchronize_session": False},
        )
        .scalars()
        .all()
    )
    counts = Counter(_utc(value).astimezone(tz).date() for value in timestamps)
    for day, count in sorted(counts.items()):
        statement = insert(ApiDailyStats).values(date=day, timezone=tz_name, request_count=count)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[ApiDailyStats.date, ApiDailyStats.timezone],
                set_={
                    "request_count": ApiDailyStats.request_count + statement.excluded.request_count
                },
            )
        )
    return len(timestamps)


def api_daily_counts(db: Session, start: datetime, end: datetime) -> list[dict]:
    """Read retained logs and archived day labels, preserving archival timezone.

    Archived statistics have daily precision. Their stored date does not shift
    when the configured timezone changes; buckets with the same date are summed.
    Only retained detailed logs can be filtered at sub-day precision.
    """
    tz_name = get_current_timezone(db)
    tz = ZoneInfo(tz_name)
    start, end = _utc(start), _utc(end)
    archived = (
        select(
            ApiDailyStats.date.label("date"), func.sum(ApiDailyStats.request_count).label("count")
        )
        .where(
            ApiDailyStats.date >= start.astimezone(tz).date(),
            ApiDailyStats.date <= end.astimezone(tz).date(),
        )
        .group_by(ApiDailyStats.date)
    )
    if db.get_bind().dialect.name == "sqlite":
        counts = Counter()
        for day, count in db.execute(archived).all():
            counts[day.isoformat()] += int(count)
        query = db.query(ApiLog.timestamp).filter(
            ApiLog.timestamp >= start, ApiLog.timestamp <= end
        )
        for (value,) in query.all():
            counts[_utc(value).astimezone(tz).date().isoformat()] += 1
        return [{"date": day, "count": counts[day]} for day in sorted(counts)]

    # One statement gives a consistent snapshot even while cleanup commits.
    day = func.date(func.timezone(tz_name, ApiLog.timestamp))
    retained = (
        select(day.label("date"), func.count(ApiLog.id).label("count"))
        .where(ApiLog.timestamp >= start, ApiLog.timestamp <= end)
        .group_by(day)
    )
    combined = union_all(archived, retained).subquery()
    rows = db.execute(
        select(combined.c.date, func.sum(combined.c.count))
        .group_by(combined.c.date)
        .order_by(combined.c.date)
    ).all()
    return [{"date": day.isoformat(), "count": int(count)} for day, count in rows]
