"""Atomic, database-backed limits for password and OTP attempts."""

from __future__ import annotations

import hashlib
import math

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models.auth_rate_limit import AuthRateLimit

IP_ATTEMPTS_PER_MINUTE = 30
IDENTITY_ATTEMPTS_PER_FIVE_MINUTES = 10


def enforce_auth_rate_limit(
    db: Session, request: Request, stage: str, identity: str | int | None
) -> None:
    """Consume attempts atomically; persist counters even when rejecting a request."""
    now = datetime.now(UTC)
    peer = request.client.host if request.client else "unknown"
    scopes = [(f"{stage}:ip:{peer}", IP_ATTEMPTS_PER_MINUTE, 60)]
    if identity is not None:
        scopes.append(
            (
                f"{stage}:identity:{str(identity).casefold()}",
                IDENTITY_ATTEMPTS_PER_FIVE_MINUTES,
                300,
            )
        )
    insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
    retry_after = 0
    try:
        # Expired windows contain only opaque hashes, and need no permanent history.
        db.execute(delete(AuthRateLimit).where(AuthRateLimit.window_end < now - timedelta(days=1)))
        for scope, limit, seconds in scopes:
            bucket = hashlib.sha256(scope.encode()).hexdigest()
            end = now + timedelta(seconds=seconds)
            expired = AuthRateLimit.window_end <= now
            statement = insert(AuthRateLimit).values(bucket=bucket, attempts=1, window_end=end)
            statement = statement.on_conflict_do_update(
                index_elements=[AuthRateLimit.bucket],
                set_={
                    "attempts": case((expired, 1), else_=AuthRateLimit.attempts + 1),
                    "window_end": case((expired, end), else_=AuthRateLimit.window_end),
                },
            ).returning(AuthRateLimit.attempts, AuthRateLimit.window_end)
            attempts, window_end = db.execute(statement).one()
            if attempts > limit:
                if window_end.tzinfo is None:
                    window_end = window_end.replace(tzinfo=UTC)
                retry_after = max(retry_after, math.ceil((window_end - now).total_seconds()), 1)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="Authentication temporarily unavailable"
        ) from exc
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts. Please try again later.",
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
