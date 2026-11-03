"""Validated recording settings and timezone-aware schedule calculations."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Literal, Self
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecordingSchedulePayload(BaseModel):
    """Configure periodic clips, an overnight-capable window, or continuous archives."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    camera_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = False
    mode: Literal["clips", "window", "continuous"] = "clips"
    interval_minutes: int = Field(default=15, ge=1, le=10080)
    cron: str = Field(default="", max_length=100)
    clip_seconds: int = Field(default=30, ge=5, le=3600)
    segment_seconds: int = Field(default=600, ge=30, le=3600)
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start_time: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(default="17:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        """Reject invalid days, unsupported cron syntax, and overlapping interval clips."""
        if len(set(self.weekdays)) != len(self.weekdays) or any(
            d < 0 or d > 6 for d in self.weekdays
        ):
            raise ValueError("Weekdays must be unique values from 0 (Monday) to 6 (Sunday)")
        if self.cron:
            trigger = CronTrigger.from_crontab(self.cron, timezone="UTC")
            try:
                future = trigger.get_next_fire_time(None, datetime.now(UTC))
            except (OSError, OverflowError) as exc:
                raise ValueError("Cron expression has no future execution") from exc
            if future is None:
                raise ValueError("Cron expression has no future execution")
        if (
            self.mode == "clips"
            and not self.cron
            and self.clip_seconds > self.interval_minutes * 60
        ):
            raise ValueError("Clip duration cannot exceed the recording interval")
        if self.mode == "window" and self.start_time == self.end_time:
            raise ValueError("Use continuous mode for a full-day recording")
        return self


def as_utc(value: datetime) -> datetime:
    """Treat legacy naive database timestamps explicitly as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def next_clip_run(schedule: RecordingSchedulePayload, now: datetime, timezone: str) -> datetime:
    """Coalesce missed runs instead of replaying an outage backlog."""
    if schedule.cron:
        trigger = CronTrigger.from_crontab(schedule.cron, timezone=ZoneInfo(timezone))
        return trigger.get_next_fire_time(None, as_utc(now) + timedelta(microseconds=1)).astimezone(
            UTC
        )
    return as_utc(now) + timedelta(minutes=schedule.interval_minutes)


def window_end(schedule: RecordingSchedulePayload, now: datetime, timezone: str) -> datetime | None:
    """Return the active window's UTC end; weekdays belong to the window's start day."""
    local = as_utc(now).astimezone(ZoneInfo(timezone))
    start = time.fromisoformat(schedule.start_time)
    end = time.fromisoformat(schedule.end_time)
    overnight = end < start
    for age in range(2 if overnight else 1):
        start_date = local.date() - timedelta(days=age)
        if start_date.weekday() not in schedule.weekdays:
            continue
        begin = local_boundary(datetime.combine(start_date, start), timezone, end=False)
        finish = local_boundary(
            datetime.combine(start_date + timedelta(days=int(overnight)), end), timezone, end=True
        )
        if begin <= as_utc(now) < finish:
            return finish
    return None


def local_boundary(value: datetime, timezone: str, *, end: bool) -> datetime:
    """Include both ambiguous folds and roll nonexistent local bounds forward."""
    zone = ZoneInfo(timezone)
    candidates = [value.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)]
    valid = [
        instant for instant in candidates if instant.astimezone(zone).replace(tzinfo=None) == value
    ]
    if not valid:
        return max(candidates)
    return max(valid) if end else min(valid)
