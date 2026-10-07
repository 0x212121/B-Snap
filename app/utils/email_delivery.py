"""Explicit notification outcomes shared by callers and the retry worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal


class EmailTransportError(RuntimeError):
    """SMTP connection or delivery failed after attempting to contact the server."""


@dataclass(frozen=True)
class EmailDeliveryResult:
    """Describe delivery without counting policy deferrals as SMTP failures."""

    status: Literal["sent", "already_sent", "deferred", "blocked", "failed", "exhausted"]
    reason: str = ""
    retry_at: datetime | None = None
    smtp_attempted: bool = False

    def __bool__(self) -> bool:
        """Preserve truth tests used by existing notification callers."""
        return self.status == "sent"


def incident_utc(value: datetime) -> datetime:
    """Treat naive database timestamps as UTC before normalizing offsets."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
