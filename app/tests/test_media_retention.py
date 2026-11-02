"""Exercise media retention against isolated, SQLite-compatible media tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.snapshot import Snapshot
from app.models.video import Video
from app.utils import media_retention, snapshot_utils


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the full-schema fixture; no application database is touched."""


@pytest.fixture
def media_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Snapshot.__table__, Video.__table__])
    with Session(engine, autoflush=False) as db:
        yield db
    engine.dispose()


def seed(db, model, camera="a", count=5, held=False):
    rows = [
        model(
            camera_id=camera,
            camera_name=camera or "Deleted camera",
            camera_ip="127.0.0.1",
            file_path=f"{camera}/{index}",
            timestamp=datetime.now(UTC) - timedelta(days=index + 1),
            retention_hold=held,
        )
        for index in range(count)
    ]
    db.add_all(rows)
    db.commit()
    return rows


@pytest.mark.parametrize("model", [Snapshot, Video])
@pytest.mark.parametrize(("mode", "expected"), [("age", 2), ("count", 3), ("both", 3)])
def test_modes_keep_newest_per_camera(media_db, monkeypatch, model, mode, expected):
    values = {
        "retention_snapshot_mode": mode,
        "retention_video_mode": mode,
        "retention_snapshot_days": 3,
        "retention_video_days": 3,
        "max_screenshot_per_camera": 2,
        "max_video_per_camera": 2,
    }
    monkeypatch.setattr(media_retention, "get_config", values.get)
    # Use noon offsets to avoid a timing race at the exact age boundary.
    rows = seed(media_db, model)
    for row in rows:
        row.timestamp += timedelta(hours=12)
    others = seed(media_db, model, camera="b", count=1)
    result = media_retention.enforce_media_retention(media_db, model)
    assert result["soft_deleted"] == expected
    assert sum(row.deleted_at is not None for row in rows) == expected
    assert rows[0].deleted_at is None
    assert others[0].deleted_at is None
    # Idempotent, including with autoflush disabled as in the application.
    media_db.commit()
    assert media_retention.enforce_media_retention(media_db, model)["soft_deleted"] == 0


@pytest.mark.parametrize("model", [Snapshot, Video])
def test_holds_deleted_rows_and_null_camera(media_db, monkeypatch, model):
    monkeypatch.setattr(
        media_retention, "get_config", lambda key, _default: "both" if key.endswith("mode") else 1
    )
    held = seed(media_db, model, camera=None, count=3, held=True)
    expired = seed(media_db, model, camera="a", count=1)[0]
    expired.soft_delete()
    media_db.commit()
    result = media_retention.enforce_media_retention(media_db, model)
    assert result["soft_deleted"] == 0
    assert result["skipped_retention_hold"] > 0
    assert all(row.deleted_at is None for row in held)


@pytest.mark.parametrize("mode", ["age", "count", "both"])
def test_capture_respects_mode(media_db, monkeypatch, mode):
    monkeypatch.setattr(
        snapshot_utils, "get_config", lambda key, _default: mode if key.endswith("mode") else 2
    )
    rows = seed(media_db, Snapshot)
    assert snapshot_utils.enforce_max_snapshots_per_camera(media_db, "a") == (
        0 if mode == "age" else 3
    )
    assert rows[0].deleted_at is None


@pytest.mark.parametrize("model", [Snapshot, Video])
def test_existing_defaults(media_db, monkeypatch, model):
    monkeypatch.setattr(media_retention, "get_config", lambda _key, default: default)
    seed(media_db, model, count=22)
    result = media_retention.enforce_media_retention(media_db, model)
    assert result["soft_deleted"] == (2 if model is Snapshot else 16)


@pytest.mark.parametrize("model", [Snapshot, Video])
def test_count_preserves_old_held_media_and_ignores_deleted(media_db, monkeypatch, model):
    monkeypatch.setattr(
        media_retention, "get_config", lambda key, _default: "count" if key.endswith("mode") else 2
    )
    rows = seed(media_db, model)
    rows[-1].retention_hold = True
    rows[-2].soft_delete()
    media_db.commit()
    result = media_retention.enforce_media_retention(media_db, model)
    assert result["soft_deleted"] == 2
    assert rows[-1].deleted_at is None
    assert rows[0].deleted_at is None
    assert rows[1].deleted_at is not None
    assert rows[2].deleted_at is not None
