"""Safe job metadata describing retry outcomes rather than SMTP credentials."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.utils.email_delivery import EmailDeliveryResult


@dataclass
class EmailRetrySummary:
    """Record counts; exhausted is a terminal subset and smtp_failed counts sends."""

    batch_size: int
    max_run_seconds: int
    selected: int = 0
    processed: int = 0
    sent: int = 0
    already_sent: int = 0
    smtp_attempted: int = 0
    smtp_failed: int = 0
    deferred: int = 0
    blocked: int = 0
    exhausted: int = 0
    cancelled: int = 0
    processing_errors: int = 0
    deferral_reasons: dict[str, int] = field(default_factory=dict)

    def record(self, result: EmailDeliveryResult, *, exhausted: bool) -> None:
        """Track a delivery result without storing recipients, config or exception text."""
        self.processed += 1
        self.smtp_attempted += int(result.smtp_attempted)
        self.smtp_failed += int(result.status == "failed" and result.smtp_attempted)
        self.exhausted += int(exhausted)
        if result.status in {"sent", "already_sent", "deferred", "blocked"}:
            setattr(self, result.status, getattr(self, result.status) + 1)
        if result.status in {"deferred", "blocked"}:
            self.defer_reason(result.reason)

    def defer_reason(self, reason: str) -> None:
        """Count stable reason codes emitted by the notification code."""
        self.deferral_reasons[reason] = self.deferral_reasons.get(reason, 0) + 1

    def complete(
        self,
        *,
        pending_total: int,
        remaining_due: int,
        oldest_pending_age_seconds: int | None,
        duration_ms: int,
        stopped_reason: str,
    ) -> dict:
        """Distinguish unsuccessful delivery, policy deferral and a successful empty run."""
        completed = self.sent + self.already_sent
        errors = self.smtp_failed + self.processing_errors + self.exhausted + self.cancelled
        incomplete = self.deferred + self.blocked + remaining_due
        if errors:
            status = "partial" if completed else "fail"
        elif incomplete:
            status = "partial" if completed else "deferred"
        else:
            status = "success"
        result = asdict(self)
        result.update(
            status=status,
            records_processed=completed,
            pending_total=pending_total,
            remaining_due=remaining_due,
            oldest_pending_age_seconds=oldest_pending_age_seconds,
            duration_ms=duration_ms,
            stopped_reason=stopped_reason,
        )
        if errors:
            result["error_message"] = (
                f"SMTP failures: {self.smtp_failed}; processing errors: {self.processing_errors}; "
                f"exhausted: {self.exhausted}; cancelled: {self.cancelled}."
            )
        return result
