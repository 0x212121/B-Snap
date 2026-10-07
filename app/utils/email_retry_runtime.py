"""Run-scoped SMTP settings and cooperative retry resource limits."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic


class EmailRetryBudgetExpiredError(RuntimeError):
    """The retry run has no time left to start another SMTP operation."""


@dataclass
class EmailRetryRuntime:
    """Cache SMTP settings only for one run and keep credentials out of repr."""

    deadline: float
    smtp_timeout_seconds: int = 30
    smtp_config: dict | None = field(default=None, repr=False)


_runtime: ContextVar[EmailRetryRuntime | None] = ContextVar("email_retry_runtime", default=None)


def current_email_retry_runtime() -> EmailRetryRuntime | None:
    """Return the current run's context without sharing it across worker threads."""
    return _runtime.get()


@contextmanager
def email_retry_runtime(deadline: float, smtp_timeout_seconds: int) -> Iterator[EmailRetryRuntime]:
    """Discard cached SMTP credentials and limits after success or failure."""
    runtime = EmailRetryRuntime(deadline, smtp_timeout_seconds)
    token = _runtime.set(runtime)
    try:
        yield runtime
    finally:
        _runtime.reset(token)


def email_smtp_timeout() -> float:
    """Cap socket waits by the run's remaining budget; standalone sends use 30s."""
    runtime = _runtime.get()
    if runtime is None:
        return 30
    remaining = runtime.deadline - monotonic()
    if remaining <= 0:
        raise EmailRetryBudgetExpiredError
    return min(runtime.smtp_timeout_seconds, remaining)
