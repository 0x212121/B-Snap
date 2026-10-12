"""Validate scheduler cron syntax and adapt it for the browser's description library."""

from __future__ import annotations

from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger


def description_expression(expression: str) -> str:
    """Preserve APScheduler semantics, including Monday-based weekday numbering."""
    trigger = CronTrigger.from_crontab(expression, timezone="UTC")
    minute, hour, day, month, weekday = expression.split()
    if weekday != "*":
        weekday_field = next(field for field in trigger.fields if field.name == "day_of_week")
        monday = datetime(2026, 10, 12)
        names = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
        weekday = ",".join(
            name
            for index, name in enumerate(names)
            if weekday_field.get_next_value(monday + timedelta(days=index)) == index
        )
    day = ",".join("L" if token.lower() == "last" else token for token in day.split(","))
    return f"{minute} {hour} {day} {month.upper()} {weekday.upper()}"
