"""Calculate observed downtime without inventing missing status history."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.models.camera_status_change_log import CameraStatusChangeLog


def as_utc(value: datetime) -> datetime:
    """Treat naive database timestamps as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def calculate_downtime(
    events: Sequence[CameraStatusChangeLog], start: datetime, end: datetime
) -> tuple[int, int]:
    """Return Offline seconds and observed seconds within a UTC window.

    Events must be chronological and include the latest transition before start,
    when available. Time before the first known state remains unobserved.
    """
    start, end = as_utc(start), as_utc(end)
    cursor = None
    state = None
    downtime = observed = 0.0
    for event in events:
        timestamp = as_utc(event.changed_at)
        if timestamp >= end:
            break
        if timestamp <= start:
            cursor, state = start, event.new_status
            continue
        if cursor is not None:
            seconds = (timestamp - cursor).total_seconds()
            observed += seconds
            if state == "Offline":
                downtime += seconds
        cursor, state = timestamp, event.new_status
    if cursor is not None:
        seconds = (end - cursor).total_seconds()
        observed += seconds
        if state == "Offline":
            downtime += seconds
    return int(downtime), int(observed)
