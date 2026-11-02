"""Shared, soft-delete retention rules for captured media."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_config
from app.models.snapshot import Snapshot
from app.models.video import Video


def enforce_media_retention(db: Session, model: type[Snapshot] | type[Video]) -> dict[str, int]:
    """Apply configured age/count limits without committing or removing files.

    Count limits include held items, which are always preserved even if that
    leaves a camera over its limit. Already deleted items do not count.
    """
    kind = "snapshot" if model is Snapshot else "video"
    default_mode = "both" if model is Snapshot else "age"
    mode = get_config(f"retention_{kind}_mode", default_mode)
    if mode not in {"age", "count", "both"}:
        raise ValueError(f"Invalid {kind} retention mode: {mode}")
    deleted = 0
    skipped = 0
    if mode in {"age", "both"}:
        days = int(get_config(f"retention_{kind}_days", 30 if model is Snapshot else 7))
        if days < 1:
            raise ValueError("Media retention days must be positive")
        expired = db.query(model).filter(
            model.deleted_at.is_(None),
            model.timestamp < datetime.now(UTC) - timedelta(days=days),
        )
        for item in expired.all():
            if item.retention_hold:
                skipped += 1
            else:
                item.soft_delete()
                deleted += 1
        # Sessions disable autoflush; count mode must see age deletions.
        db.flush()
    if mode in {"count", "both"}:
        key = "max_screenshot_per_camera" if model is Snapshot else "max_video_per_camera"
        limit = int(get_config(key, 20))
        if limit < 1:
            raise ValueError("Media retention count must be positive")
        cameras = db.query(model.camera_id).filter(model.deleted_at.is_(None)).distinct().all()
        for (camera_id,) in cameras:
            result = enforce_count_retention(db, model, camera_id, limit)
            deleted += result["soft_deleted"]
            skipped += result["skipped_retention_hold"]
    return {"soft_deleted": deleted, "skipped_retention_hold": skipped}


def enforce_count_retention(
    db: Session, model: type[Snapshot] | type[Video], camera_id: str | None, limit: int
) -> dict[str, int]:
    """Soft-delete oldest unheld media exceeding one camera's active count limit."""
    if limit < 1:
        raise ValueError("Media retention count must be positive")
    active = db.query(model).filter(model.camera_id == camera_id, model.deleted_at.is_(None))
    excess = max(0, active.count() - limit)
    candidates = (
        (
            active.filter(model.retention_hold.is_(False))
            .order_by(model.timestamp.asc(), model.id.asc())
            .limit(excess)
            .all()
        )
        if excess
        else []
    )
    for item in candidates:
        item.soft_delete()
    return {"soft_deleted": len(candidates), "skipped_retention_hold": excess - len(candidates)}
