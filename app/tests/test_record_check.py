from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.record_check import (
    RecordCheckRun,
    RecordFolderCheck,
    RecordFolderStatus,
    RecordSource,
    RecordStatusEvent,
)
from app.utils.record_check import classify_record_folder, validate_record_source_path
from app.utils.record_check_report import _tz, build_14_day_trend


def test_classify_record_folder_threshold_boundaries():
    assert classify_record_folder(4200, 4200, 604800) == "healthy"
    assert classify_record_folder(4201, 4200, 604800) == "stale"
    assert classify_record_folder(604800, 4200, 604800) == "long_dead"


def test_classify_record_folder_unknown_without_mtime():
    assert classify_record_folder(None, 4200, 604800) == "unknown"


def test_validate_record_source_path_requires_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        validate_record_source_path("relative/path")


def test_validate_record_source_path_accepts_absolute_path(tmp_path: Path):
    assert validate_record_source_path(str(tmp_path)) == tmp_path


def test_14_day_trend_starts_at_first_available_check(db_session: Session):
    """A new source must not accrue downtime before its first saved check."""
    tz = _tz()
    first_check_at = tz.localize(datetime(2026, 7, 23, 8, 10)).astimezone(timezone.utc)
    as_of = first_check_at + timedelta(minutes=30)
    source = RecordSource(name="Trend source", base_path="C:/records")
    db_session.add(source)
    db_session.flush()

    run = RecordCheckRun(source_id=source.id, started_at=first_check_at, status="success")
    status = RecordFolderStatus(
        source_id=source.id,
        folder_name="ch01",
        folder_path="C:/records/ch01",
        status="stale",
        status_changed_at=first_check_at,
        last_checked_at=first_check_at,
        alert_active=True,
    )
    db_session.add_all([run, status])
    db_session.flush()
    db_session.add_all(
        [
            RecordFolderCheck(
                run_id=run.id,
                source_id=source.id,
                folder_name="ch01",
                folder_path="C:/records/ch01",
                status="stale",
                checked_at=first_check_at,
            ),
            RecordStatusEvent(
                source_id=source.id,
                folder_status_id=status.id,
                folder_name="ch01",
                new_status="stale",
                event_type="record_alert",
                created_at=first_check_at,
            ),
        ]
    )
    db_session.commit()

    trend = build_14_day_trend(db_session, source.id, as_of=as_of)

    assert len(trend) == 1
    assert trend[0]["date"] == "2026-07-23"
    assert trend[0]["downtime_minutes"] == 30.0
