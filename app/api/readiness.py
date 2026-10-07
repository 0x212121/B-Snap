"""Minimal unauthenticated readiness probe for container monitoring."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.database import engine

router = APIRouter()


@router.get("/readyz", include_in_schema=False)
def readiness(request: Request) -> JSONResponse:
    """Report startup completion and live database availability without details."""
    ready = bool(getattr(request.app.state, "ready", False))
    if ready:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            ready = False
    return JSONResponse(
        {"status": "ready" if ready else "unavailable"}, status_code=200 if ready else 503
    )
