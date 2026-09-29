# app/routes/insights.py
import logging
from xml.sax.saxutils import escape
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.log import ApiLog, CommandLog
from app.models.camera_email_notification_log import CameraEmailNotificationLog
from app.models.camera_group import CameraGroup
from app.utils.template_helper import templates
from datetime import datetime, timedelta, timezone
import pytz
from app.utils.timezone_helper import get_current_timezone
from app.routes.auth import admin_access_required
from app.models.user import User
from app.routes.stats import get_camera_operations_data
from io import BytesIO

# --- ReportLab for PDF ---
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.lib.utils import ImageReader
import os

# Get project root directory for resolving static files
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO_PATH = os.path.join(PROJECT_ROOT, "static", "icons", "logo.png")

router = APIRouter(tags=["Observability"])

# setup logger for this module
logger = logging.getLogger("app.insights")


# --------- Helpers ---------
def _local_tz(db: Session = None):
    # Ambil timezone dari config database, fallback ke sistem
    if db:
        tz_name = get_current_timezone(db)
        return pytz.timezone(tz_name)
    return datetime.now().astimezone().tzinfo


def _parse_range(start_date: str | None, end_date: str | None, db: Session = None):
    tz = _local_tz(db)
    now = datetime.now(tz)

    if not start_date or not end_date:
        end_dt = now
        start_dt = now - timedelta(days=30)
    else:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)

        if start_dt.tzinfo is None:
            start_dt = tz.localize(start_dt)
        if end_dt.tzinfo is None:
            end_dt = tz.localize(end_dt)

        if end_dt < start_dt:
            raise HTTPException(status_code=422, detail="end_date must be on or after start_date")

        end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    # convert ke UTC untuk query DB
    start_dt_utc = start_dt.astimezone(pytz.UTC)
    end_dt_utc = end_dt.astimezone(pytz.UTC)

    span = end_dt_utc - start_dt_utc
    prev_end = start_dt_utc - timedelta(microseconds=1)
    prev_start = prev_end - span

    return start_dt_utc, end_dt_utc, prev_start, prev_end


def _daily_counts(db: Session, model, ts_field, start_dt, end_dt, success_only=False):
    tz_name = get_current_timezone(db)

    if db.get_bind().dialect.name == "sqlite":
        tz = pytz.timezone(tz_name)
        timestamp_column = getattr(model, ts_field)
        query = db.query(timestamp_column).filter(
            timestamp_column >= start_dt,
            timestamp_column <= end_dt,
        )
        if success_only and hasattr(model, "success"):
            query = query.filter(model.success.is_(True))

        counts = {}
        for (timestamp,) in query.all():
            if timestamp is None:
                continue
            if timestamp.tzinfo is None:
                timestamp = pytz.UTC.localize(timestamp)
            local_day = timestamp.astimezone(tz).date().isoformat()
            counts[local_day] = counts.get(local_day, 0) + 1
        return [{"date": day, "count": counts[day]} for day in sorted(counts)]

    # convert ke local timezone sebelum DATE()
    date_expr = func.date(
        func.timezone(tz_name, getattr(model, ts_field))
    )

    query = (
        db.query(date_expr.label("date"), func.count("*").label("count"))
        .filter(
            getattr(model, ts_field) >= start_dt,
            getattr(model, ts_field) <= end_dt
        )
    )

    if success_only and hasattr(model, 'success'):
        query = query.filter(model.success == True)

    rows = query.group_by(date_expr).order_by(date_expr).all()
    return [{"date": str(r.date), "count": r.count} for r in rows]


def _total_count(db: Session, model, ts_field, start_dt, end_dt, success_only=False):
    query = db.query(func.count("*")).filter(
        getattr(model, ts_field) >= start_dt,
        getattr(model, ts_field) <= end_dt
    )
    # Filter success=True untuk email logs jika diminta
    if success_only and hasattr(model, 'success'):
        query = query.filter(model.success == True)
    return query.scalar() or 0


def _pct_change(current: int, previous: int, days_now: int, days_prev: int) -> float:
    """
    Hitung perubahan persentase berbasis rata-rata per hari.
    Misal range 4 hari dibandingkan dengan 4 hari sebelumnya.
    """
    # defensive: ensure ints
    current = int(current or 0)
    previous = int(previous or 0)

    if previous == 0:
        # Jika periode sebelumnya kosong, definisikan kenaikan 100% bila ada aktivitas sekarang
        return 100.0 if current > 0 else 0.0

    # rata-rata per hari
    avg_now = current / max(days_now, 1)
    avg_prev = previous / max(days_prev, 1)

    if avg_prev == 0:
        return 100.0 if avg_now > 0 else 0.0

    return (avg_now - avg_prev) * 100.0 / avg_prev


def _activity_trend_index(api_now, api_prev, cmd_now, cmd_prev, email_now, email_prev,
                          days_now: int = 1, days_prev: int = 1) -> tuple[int, str, str]:
    """
    Summarize activity changes on a 0–100 scale; this is not a system health score.
    Returns (index, trend label, color key).
    """
    import math

    def squash(pct):
        # pct diharapkan dalam persen (mis. 25.0)
        x = pct / 100.0
        return math.tanh(x)

    api_score = squash(_pct_change(api_now, api_prev, days_now, days_prev))
    cmd_score = squash(_pct_change(cmd_now, cmd_prev, days_now, days_prev))
    email_score = squash(_pct_change(email_now, email_prev, days_now, days_prev))  # email_now/prev sudah filtered success_only

    avg = (api_score + cmd_score + email_score) / 3.0
    # map -1..1 -> 0..100
    idx = int(round((avg + 1.0) * 50.0))

    if idx >= 55:
        label, color = "Increasing", "blue"
    elif idx <= 45:
        label, color = "Decreasing", "amber"
    else:
        label, color = "Stable", "gray"

    return idx, label, color


# --------- Pages ---------
@router.get("/insights", include_in_schema=False)
def insights_dashboard_redirect(request: Request):
    """Redirect old /insights to /analytics for backwards compatibility."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/analytics?tab=overview", status_code=301)


# --------- APIs for charts/data ---------
@router.get("/log/stats/daily")
def get_daily_stats(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required),
):
    """Daily aggregated counts for API, Command, and Email (date-range aware)."""
    start_dt, end_dt, _, _ = _parse_range(start_date, end_date, db)

    api = _daily_counts(db, ApiLog, "timestamp", start_dt, end_dt)
    cmd = _daily_counts(db, CommandLog, "timestamp", start_dt, end_dt)
    email = _daily_counts(db, CameraEmailNotificationLog, "sent_at", start_dt, end_dt, success_only=True)

    logger.debug("get_daily_stats: start=%s end=%s api_days=%d cmd_days=%d email_days=%d",
                 start_dt.isoformat(), end_dt.isoformat(), len(api), len(cmd), len(email))

    return {"api": api, "command": cmd, "email": email}


@router.get("/log/stats/top-commands")
def get_top_commands(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    current_admin: User = Depends(admin_access_required),
):
    """Top-N most executed commands (date-range aware)."""
    start_dt, end_dt, _, _ = _parse_range(start_date, end_date, db)

    results = (
        db.query(
            CommandLog.command,
            func.count(CommandLog.id).label("count")
        )
        .filter(CommandLog.timestamp >= start_dt, CommandLog.timestamp <= end_dt)
        .group_by(CommandLog.command)
        .order_by(func.count(CommandLog.id).desc())
        .limit(limit)
        .all()
    )
    logger.debug("get_top_commands: start=%s end=%s limit=%d results=%d",
                 start_dt.isoformat(), end_dt.isoformat(), limit, len(results))
    return [{"command": r.command, "count": r.count} for r in results]


@router.get("/log/stats/top-cameras")
def get_top_cameras_by_email(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    limit: int = Query(5, ge=1, le=50),
    current_admin: User = Depends(admin_access_required),
):
    """Top-N cameras by email notifications sent (date-range aware)."""
    start_dt, end_dt, _, _ = _parse_range(start_date, end_date, db)

    rows = (
        db.query(
            CameraEmailNotificationLog.camera_name,
            func.count(CameraEmailNotificationLog.id).label("count")
        )
        .filter(
            CameraEmailNotificationLog.sent_at >= start_dt,
            CameraEmailNotificationLog.sent_at <= end_dt,
            CameraEmailNotificationLog.success == True  # Only count successful emails
        )
        .group_by(CameraEmailNotificationLog.camera_name)
        .order_by(func.count(CameraEmailNotificationLog.id).desc())
        .limit(limit)
        .all()
    )
    logger.debug("get_top_cameras_by_email: start=%s end=%s limit=%d results=%d",
                 start_dt.isoformat(), end_dt.isoformat(), limit, len(rows))
    return [{"camera_name": r.camera_name or "(unknown)", "count": r.count} for r in rows]


@router.get("/log/stats/summary")
def get_summary(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required),
):
    """KPI summary, change vs previous period, and activity trend index."""
    try:
        logger.info("get_summary called: start_date=%s end_date=%s", start_date, end_date)
        start_dt, end_dt, prev_start, prev_end = _parse_range(start_date, end_date, db)
        logger.debug("Parsed ranges: start=%s end=%s prev_start=%s prev_end=%s",
                     start_dt.isoformat(), end_dt.isoformat(), prev_start.isoformat(), prev_end.isoformat())

        # Totals current
        api_now = _total_count(db, ApiLog, "timestamp", start_dt, end_dt)
        cmd_now = _total_count(db, CommandLog, "timestamp", start_dt, end_dt)
        email_now = _total_count(db, CameraEmailNotificationLog, "sent_at", start_dt, end_dt, success_only=True)

        # Totals previous
        api_prev = _total_count(db, ApiLog, "timestamp", prev_start, prev_end)
        cmd_prev = _total_count(db, CommandLog, "timestamp", prev_start, prev_end)
        email_prev = _total_count(db, CameraEmailNotificationLog, "sent_at", prev_start, prev_end, success_only=True)

        logger.info("Totals - now: api=%d cmd=%d email=%d | prev: api=%d cmd=%d email=%d",
                    api_now, cmd_now, email_now, api_prev, cmd_prev, email_prev)

        # Hitung jumlah hari pada masing-masing periode
        days_now = (end_dt - start_dt).days + 1
        days_prev = (prev_end - prev_start).days + 1
        logger.debug("Days - now=%d prev=%d", days_now, days_prev)

        # Delta % berdasarkan rata-rata per hari
        api_delta = _pct_change(api_now, api_prev, days_now, days_prev)
        cmd_delta = _pct_change(cmd_now, cmd_prev, days_now, days_prev)
        email_delta = _pct_change(email_now, email_prev, days_now, days_prev)
        logger.info("Deltas (%%) - api=%.2f cmd=%.2f email=%.2f", api_delta, cmd_delta, email_delta)

        # Activity trend index (normalized by period length).
        idx, label, color = _activity_trend_index(
            api_now, api_prev, cmd_now, cmd_prev, email_now, email_prev,
            days_now=days_now, days_prev=days_prev
        )
        logger.info("ActivityTrendIndex - value=%d label=%s color=%s", idx, label, color)

        # Insight naratif sederhana (tetap disediakan untuk PDF; FE bisa abaikan)
        insights = []

        def trend_txt(name, now, prev, delta):
            arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
            return f"{name}: {now:,} ({arrow} {delta:.1f}% vs prev {prev:,})."

        insights.append(trend_txt("API calls", api_now, api_prev, api_delta))
        insights.append(trend_txt("Commands", cmd_now, cmd_prev, cmd_delta))
        insights.append(trend_txt("Emails", email_now, email_prev, email_delta))

        return {
            "range": {"start": start_dt.isoformat(), "end": end_dt.isoformat(),
                      "prev_start": prev_start.isoformat(), "prev_end": prev_end.isoformat()},
            "totals": {"api": api_now, "command": cmd_now, "email": email_now},
            "previous": {"api": api_prev, "command": cmd_prev, "email": email_prev},
            "delta_pct": {"api": api_delta, "command": cmd_delta, "email": email_delta},
            "activity_index": {"value": idx, "label": label, "color": color},
            # Keep the old key for clients that consumed the original summary shape.
            "health_index": {"value": idx, "label": label, "color": color},
            "insights": insights,
        }
    except Exception:
        logger.exception("Unhandled error in get_summary")
        raise


@router.get("/log/stats/dashboard")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required),
):
    """Bundle the overview's summary, trends, and top lists into one response."""
    return {
        "summary": get_summary(db, start_date, end_date, current_admin),
        "daily": get_daily_stats(db, start_date, end_date, current_admin),
        "top_commands": get_top_commands(db, start_date, end_date, 10, current_admin),
        "top_cameras": get_top_cameras_by_email(db, start_date, end_date, 5, current_admin),
    }


@router.post("/log/stats/report-pro")
async def get_executive_report_pro(
    request: Request,
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    current_admin: User = Depends(admin_access_required),
):
    """
    Generate professional PDF report with embedded charts and formal executive narration.
    """
    form = await request.form()
    api_chart = form.get("api_chart")
    email_chart = form.get("email_chart")
    topcmd_chart = form.get("topcmd_chart")
    availability_chart = form.get("availability_chart")
    incidents_chart = form.get("incidents_chart")
    group_id_value = form.get("group_id")
    try:
        group_id = int(group_id_value) if group_id_value else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid camera group") from exc

    if not start_date:
        start_date = form.get("start_date")
    if not end_date:
        end_date = form.get("end_date")

    logger.info("Report-Pro request: %s → %s", start_date, end_date)

    # --- Data Retrieval ---
    start_dt, end_dt, prev_start, prev_end = _parse_range(start_date, end_date, db)
    report_timezone = _local_tz(db)
    operations_start_date = start_dt.astimezone(report_timezone).date()
    operations_end_date = end_dt.astimezone(report_timezone).date()
    operations = await get_camera_operations_data(
        request,
        operations_start_date,
        operations_end_date,
        group_id,
        db,
        current_admin,
    )
    camera_group = db.get(CameraGroup, group_id) if group_id is not None else None
    if group_id is not None and camera_group is None:
        raise HTTPException(status_code=404, detail="Camera group not found")
    
    # Get totals
    api_now = _total_count(db, ApiLog, "timestamp", start_dt, end_dt)
    cmd_now = _total_count(db, CommandLog, "timestamp", start_dt, end_dt)
    email_now = _total_count(db, CameraEmailNotificationLog, "sent_at", start_dt, end_dt, success_only=True)
    api_prev = _total_count(db, ApiLog, "timestamp", prev_start, prev_end)
    cmd_prev = _total_count(db, CommandLog, "timestamp", prev_start, prev_end)
    email_prev = _total_count(db, CameraEmailNotificationLog, "sent_at", prev_start, prev_end, success_only=True)
    
    # Calculate deltas and activity trend index.
    days_now = (end_dt - start_dt).days + 1
    days_prev = (prev_end - prev_start).days + 1
    api_delta = _pct_change(api_now, api_prev, days_now, days_prev)
    cmd_delta = _pct_change(cmd_now, cmd_prev, days_now, days_prev)
    email_delta = _pct_change(email_now, email_prev, days_now, days_prev)
    idx, label, color = _activity_trend_index(
        api_now,
        api_prev,
        cmd_now,
        cmd_prev,
        email_now,
        email_prev,
        days_now=days_now,
        days_prev=days_prev,
    )
    
    totals = {"api": api_now, "command": cmd_now, "email": email_now}
    deltas = {"api": api_delta, "command": cmd_delta, "email": email_delta}
    hi = {"value": idx, "label": label, "color": color}
    previous = {"api": api_prev, "command": cmd_prev, "email": email_prev}
    
    top_cmd = get_top_commands(db, start_date, end_date, limit=10)
    top_cam = get_top_cameras_by_email(db, start_date, end_date, limit=5)

    def pct_phrase(value: float) -> str:
        """Convert delta% to readable text like 'meningkat 12.3%' or 'menurun 5.6%'."""
        if value > 0.2:
            return f"meningkat {value:.1f}%"
        elif value < -0.2:
            return f"menurun {abs(value):.1f}%"
        return "stabil"

    # --- Executive Summary (formal style) ---
    summary_text = (
        f"Selama periode {start_dt.strftime('%d %B %Y')} hingga {end_dt.strftime('%d %B %Y')}, "
        f"sistem B-SNAP mencatat total {totals['api']:,} panggilan API, "
        f"{totals['command']:,} eksekusi perintah, dan "
        f"{totals['email']:,} email notifikasi terkirim. "
        f"Dibandingkan periode sebelumnya, aktivitas API {pct_phrase(deltas['api'])}, "
        f"eksekusi perintah {pct_phrase(deltas['command'])}, dan "
        f"notifikasi email {pct_phrase(deltas['email'])}. "
        f"Indeks tren aktivitas berada pada nilai {hi['value']}/100 dengan tren {hi['label'].lower()}."
    )

    highlights = [
        f"API Calls: {pct_phrase(deltas['api']).capitalize()} dibanding periode sebelumnya.",
        f"Commands Executed: {pct_phrase(deltas['command']).capitalize()} dibanding periode sebelumnya.",
        f"Email Notifications: {pct_phrase(deltas['email']).capitalize()} dibanding periode sebelumnya.",
    ]

    top_command_txt = (
        f"Perintah yang paling sering dieksekusi adalah “{top_cmd[0]['command']}” "
        f"dengan total {top_cmd[0]['count']} kali eksekusi."
        if top_cmd else "Tidak terdapat data perintah untuk periode ini."
    )
    top_camera_txt = (
        f"Kamera dengan notifikasi email terbanyak adalah “{top_cam[0]['camera_name']}” "
        f"dengan {top_cam[0]['count']} pengiriman."
        if top_cam else "Tidak terdapat data kamera untuk periode ini."
    )

    # --- Build PDF ---
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )

    doc.title = f"B-SNAP Executive Report – {start_dt.date()} to {end_dt.date()}"
    doc.author = "B-SNAP Automated Observability System"
    doc.subject = "Executive Summary and Key Metrics Report"
    styles = getSampleStyleSheet()
    elems = []

    # --- Header ---
    logo_path = LOGO_PATH if os.path.exists(LOGO_PATH) else "/app/static/icons/logo.png"
    try:
        if os.path.exists(logo_path):
            elems.append(RLImage(logo_path, width=3 * cm, height=3 * cm))
    except Exception:
        pass
    report_title = f"B-SNAP Executive Report"
    elems.append(Paragraph(report_title, styles["Title"]))
    elems.append(Paragraph(f"Periode: {start_dt.date()} – {end_dt.date()}", styles["Normal"]))
    group_label = escape(camera_group.name) if camera_group else "Semua grup"
    elems.append(Paragraph(
        f"Cakupan metrik kamera: {group_label}. Metrik aktivitas sistem dihitung secara global.",
        styles["Normal"],
    ))
    elems.append(Spacer(1, 0.5 * cm))

    # --- Executive Summary Section ---
    elems.append(Paragraph("<b>Executive Summary</b>", styles["Heading2"]))
    elems.append(Paragraph(summary_text, styles["Normal"]))
    elems.append(Spacer(1, 0.3 * cm))

    # --- Key Highlights Section ---
    elems.append(Paragraph("<b>Key Highlights</b>", styles["Heading3"]))
    for h in highlights:
        elems.append(Paragraph(f"• {h}", styles["Normal"]))
    elems.append(Spacer(1, 0.3 * cm))

    # --- Top Performers Section ---
    elems.append(Paragraph("<b>Top Performers</b>", styles["Heading3"]))
    elems.append(Paragraph(top_command_txt, styles["Normal"]))
    elems.append(Paragraph(top_camera_txt, styles["Normal"]))
    elems.append(Spacer(1, 0.6 * cm))

    # --- KPI Table ---
    kpi_data = [
        ["Metric", "Current", "Previous", "Δ %"],
        ["API Calls", f"{totals['api']:,}", f"{previous['api']:,}", f"{deltas['api']:.1f}%"],
        ["Commands", f"{totals['command']:,}", f"{previous['command']:,}", f"{deltas['command']:.1f}%"],
        ["Emails", f"{totals['email']:,}", f"{previous['email']:,}", f"{deltas['email']:.1f}%"],
    ]
    kpi_tbl = Table(kpi_data, colWidths=[5 * cm, 3 * cm, 3 * cm, 3 * cm])
    kpi_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(kpi_tbl)
    elems.append(Spacer(1, 0.2 * cm))
    elems.append(Paragraph("<i>Note: Δ% calculated per-day average basis</i>", styles["Normal"]))
    elems.append(Spacer(1, 0.5 * cm))

    # --- Activity Trend Index ---
    color_map = {"blue": colors.blue, "amber": colors.orange, "gray": colors.grey}
    color = color_map.get(hi["color"], colors.grey)
    hi_tbl = Table(
        [["Activity Trend Index", f"{hi['value']}/100 – {hi['label']}"]],
        colWidths=[6 * cm, 8 * cm],
        style=[
            ("BACKGROUND", (0, 0), (0, 0), color),
            ("TEXTCOLOR", (0, 0), (0, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ],
    )
    elems.append(hi_tbl)
    elems.append(Spacer(1, 0.6 * cm))

    total_uptime_seconds = operations["total_uptime_seconds"]
    total_downtime_seconds = operations["total_downtime_seconds"]
    monitored_seconds = total_uptime_seconds + total_downtime_seconds
    availability_value = (
        total_uptime_seconds / monitored_seconds * 100 if monitored_seconds else None
    )
    operations_data = [
        ["Camera Operations", "Selected period"],
        ["Availability", f"{availability_value:.2f}%" if availability_value is not None else "No monitoring data"],
        ["Monitored downtime", f"{total_downtime_seconds / 3600:.2f} hours" if monitored_seconds else "No monitoring data"],
        ["Offline transitions", f"{operations['total_offline_incidents']:,}"],
    ]
    operations_table = Table(operations_data, colWidths=[7 * cm, 7 * cm])
    operations_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(Paragraph("<b>Camera Operations Summary</b>", styles["Heading2"]))
    elems.append(operations_table)
    elems.append(Spacer(1, 0.5 * cm))

    # --- Charts ---
    def add_chart(b64_str, caption):
        if not b64_str:
            return
        try:
            import base64
            imgdata = base64.b64decode(b64_str.split(",")[1])
            reader = ImageReader(BytesIO(imgdata))
            elems.append(RLImage(reader, width=14 * cm, height=7 * cm))
            elems.append(Paragraph(caption, styles["Normal"]))
            elems.append(Spacer(1, 0.4 * cm))
        except Exception:
            logger.exception("Chart embedding failed")

    add_chart(api_chart, "API vs Command Logs (Daily Trend)")
    add_chart(email_chart, "Email Notifications (Daily Count)")
    add_chart(topcmd_chart, "Top 10 Most Executed Commands")
    add_chart(availability_chart, f"Camera Availability and Downtime ({group_label})")
    add_chart(incidents_chart, f"Offline Transitions ({group_label})")

    elems.append(Paragraph("<b>Top Cameras by Downtime</b>", styles["Heading3"]))
    downtime_data = [["Camera", "Downtime (hours)", "Availability"]]
    downtime_data.extend([
        [
            row["camera_name"],
            f'{row["downtime_hours"]:.2f}',
            f'{row["availability"]:.2f}%' if row["availability"] is not None else "No data",
        ]
        for row in operations["top_downtime_cameras"]
    ])
    downtime_table = Table(downtime_data, colWidths=[7 * cm, 4 * cm, 4 * cm], repeatRows=1)
    downtime_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(downtime_table)
    elems.append(Spacer(1, 0.5 * cm))

    # --- Top Tables ---
    elems.append(Paragraph("<b>Top 5 Cameras by Email (Global)</b>", styles["Heading3"]))
    cam_data = [["Camera", "Emails"]] + [[r["camera_name"], f'{r["count"]:,}'] for r in top_cam]
    cam_tbl = Table(cam_data, colWidths=[9 * cm, 5 * cm])
    cam_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(cam_tbl)
    elems.append(Spacer(1, 0.4 * cm))

    elems.append(Paragraph("<b>Top 10 Commands</b>", styles["Heading3"]))
    cmd_data = [["Command", "Count"]] + [[r["command"], f'{r["count"]:,}'] for r in top_cmd]
    cmd_tbl = Table(cmd_data, colWidths=[9 * cm, 5 * cm])
    cmd_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elems.append(cmd_tbl)
    elems.append(Spacer(1, 0.5 * cm))

    elems.append(Paragraph(
        "Generated automatically by B-SNAP Insights Module • Confidential",
        styles["Normal"]
    ))

    doc.build(elems)
    buf.seek(0)
    filename = f"b-snap-report-pro_{start_dt.date()}_{end_dt.date()}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

