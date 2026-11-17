"""Date bounds for snapshot gallery use local calendar days before pagination."""

from datetime import UTC, date, datetime

import pytest

from fastapi import HTTPException

from app.models.snapshot import Snapshot
from app.utils.video_dates import filter_media_dates


class QueryCapture:
    def __init__(self) -> None:
        self.conditions = []

    def filter(self, condition):
        self.conditions.append(condition)
        return self


@pytest.mark.parametrize(
    ("zone", "begin", "finish"),
    [
        (
            "Asia/Jakarta",
            datetime(2026, 11, 2, 17, tzinfo=UTC),
            datetime(2026, 11, 3, 17, tzinfo=UTC),
        ),
        (
            "Asia/Makassar",
            datetime(2026, 11, 2, 16, tzinfo=UTC),
            datetime(2026, 11, 3, 16, tzinfo=UTC),
        ),
        (
            "Asia/Jayapura",
            datetime(2026, 11, 2, 15, tzinfo=UTC),
            datetime(2026, 11, 3, 15, tzinfo=UTC),
        ),
        (
            "America/New_York",
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
        ),
    ],
)
def test_local_day_bounds(zone: str, begin: datetime, finish: datetime) -> None:
    selected = date(2026, 11, 1) if zone == "America/New_York" else date(2026, 11, 3)
    query = QueryCapture()
    filter_media_dates(query, Snapshot.timestamp, selected, selected, zone)
    assert [condition.right.value for condition in query.conditions] == [begin, finish]


def test_open_and_invalid_ranges() -> None:
    query = QueryCapture()
    filter_media_dates(query, Snapshot.timestamp, None, date(2026, 11, 3), "Asia/Makassar")
    assert len(query.conditions) == 1
    with pytest.raises(HTTPException) as error:
        filter_media_dates(query, Snapshot.timestamp, date(2026, 11, 4), date(2026, 11, 3), "UTC")
    assert error.value.status_code == 400
