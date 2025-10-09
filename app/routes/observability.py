# app/routes/observability.py
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.db.database import get_db
from app.models.log import ApiLog, CommandLog
from app.utils.template_helper import templates

router = APIRouter(tags=["Observability"])

# --- Aggregated API ---
@router.get("/observability", include_in_schema=False)
def observability_dashboard(request: Request, db: Session = Depends(get_db)):
    """Render observability dashboard (daily usage summary)."""
    return templates.TemplateResponse("observability.html", {"request": request})

@router.get("/log/stats/daily")
def get_daily_stats(db: Session = Depends(get_db)):
    """Return daily aggregated counts for API and Command logs."""
    # limit to last 30 days
    cutoff = datetime.utcnow() - timedelta(days=30)

    api_data = (
        db.query(func.date(ApiLog.timestamp).label("date"), func.count(ApiLog.id).label("count"))
        .filter(ApiLog.timestamp >= cutoff)
        .group_by(func.date(ApiLog.timestamp))
        .order_by(func.date(ApiLog.timestamp))
        .all()
    )

    cmd_data = (
        db.query(func.date(CommandLog.timestamp).label("date"), func.count(CommandLog.id).label("count"))
        .filter(CommandLog.timestamp >= cutoff)
        .group_by(func.date(CommandLog.timestamp))
        .order_by(func.date(CommandLog.timestamp))
        .all()
    )

    # format to JSON-friendly list
    api_stats = [{"date": str(r.date), "count": r.count} for r in api_data]
    cmd_stats = [{"date": str(r.date), "count": r.count} for r in cmd_data]

    return {"api": api_stats, "command": cmd_stats}


@router.get("/log/stats/top-commands")
def get_top_commands(db: Session = Depends(get_db)):
    """Return top 10 most executed commands."""
    results = (
        db.query(
            CommandLog.command,
            func.count(CommandLog.id).label("count")
        )
        .group_by(CommandLog.command)
        .order_by(func.count(CommandLog.id).desc())
        .limit(10)
        .all()
    )

    return [{"command": r.command, "count": r.count} for r in results]
