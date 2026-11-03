"""Filter archive days using the configured clock and UTC database instants."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Query

from app.models.video import Video


def filter_video_dates(query: Query, start: date | None, end: date | None, timezone: str) -> Query:
    """Include both selected local calendar days without shifting machine date inputs."""
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="Start date must not be after end date")
    zone = ZoneInfo(timezone)
    try:
        if start:
            begin = datetime.combine(start, time.min, tzinfo=zone).astimezone(UTC)
            query = query.filter(Video.timestamp >= begin)
        if end:
            finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone).astimezone(
                UTC
            )
            query = query.filter(Video.timestamp < finish)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(
            status_code=400, detail="Date range is outside supported limits"
        ) from exc
    return query
