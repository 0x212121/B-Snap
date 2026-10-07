from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytz
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_config
from app.models.record_check import (
    RecordFolderCheck,
    RecordFolderStatus,
    RecordSource,
    RecordStatusEvent,
)
from app.utils.wa_gateway import WAGatewayService, format_phone_number

logger = logging.getLogger("record_check")

PROBLEM_EVENTS = {"record_alert", "folder_missing"}
RECOVERY_EVENTS = {"record_recovery"}


@dataclass
class DowntimeInterval:
    source_id: str
    source_name: str
    nvr_name: str | None
    folder_name: str
    camera_name: str | None
    problem_at: datetime
    recovery_at: datetime | None
    duration_seconds: int
    active: bool = False


@dataclass
class SourceDailyReport:
    source: RecordSource
    report_start: datetime
    report_end: datetime
    intervals: list[DowntimeInterval]
    normal_count: int
    active_problem_count: int
    inactive_count: int
    total_count: int
    trend_rows: list[dict] = field(default_factory=list)


def _tz() -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(get_config("timezone", "Asia/Makassar"))
    except Exception:
        return pytz.timezone("Asia/Makassar")


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _fmt_report_date(value: datetime, *, include_timezone: bool = False) -> str:
    local_value = value.astimezone(_tz())
    formatted = f"{local_value.day}/{local_value.month}/{local_value.year}, {local_value:%H.%M.%S}"
    if include_timezone:
        return f"{formatted} {local_value:%Z}"
    return formatted


def _fmt_event_time(value: datetime) -> str:
    return value.astimezone(_tz()).strftime("%d/%m, %H.%M")


def _duration_label(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    remainder = minutes % 60
    if remainder:
        return f"{hours} hours {remainder} minutes"
    return f"{hours} hours"


def _channel_label(folder_name: str, camera_name: str | None) -> str:
    return f"{folder_name} - {camera_name}" if camera_name else folder_name


def get_report_window(days_back: int = 0) -> tuple[datetime, datetime]:
    tz = _tz()
    now = datetime.now(tz)
    target_date = now.date() - timedelta(days=days_back)
    start = tz.localize(datetime.combine(target_date, time.min)).astimezone(timezone.utc)
    # A report for today must only include elapsed time.  Using time.max here
    # projected active downtime into the future until 23:59:59.
    end = now.astimezone(timezone.utc) if days_back == 0 else tz.localize(
        datetime.combine(target_date, time.max)
    ).astimezone(timezone.utc)
    return start, end


def get_record_downtime_intervals(
    db: Session,
    start_at: datetime,
    end_at: datetime,
    source_id: str | None = None,
) -> list[DowntimeInterval]:
    start_at = _as_aware(start_at)
    end_at = _as_aware(end_at)

    query = (
        db.query(RecordStatusEvent)
        .options(
            joinedload(RecordStatusEvent.source).joinedload(RecordSource.nvr),
            joinedload(RecordStatusEvent.camera),
        )
        .filter(
            RecordStatusEvent.created_at <= end_at,
            RecordStatusEvent.event_type.in_(list(PROBLEM_EVENTS | RECOVERY_EVENTS)),
        )
    )
    if source_id:
        query = query.filter(RecordStatusEvent.source_id == source_id)

    events = query.order_by(
        RecordStatusEvent.source_id.asc(),
        RecordStatusEvent.folder_name.asc(),
        RecordStatusEvent.created_at.asc(),
    ).all()

    open_problem: dict[tuple[str, str], RecordStatusEvent] = {}
    intervals: list[DowntimeInterval] = []
    latest_event_by_key: dict[tuple[str, str], RecordStatusEvent] = {}

    for event in events:
        key = (event.source_id, event.folder_name.strip().lower())
        latest_event_by_key[key] = event
        if event.event_type in PROBLEM_EVENTS:
            if key not in open_problem:
                open_problem[key] = event
            continue

        if event.event_type in RECOVERY_EVENTS and key in open_problem:
            problem = open_problem.pop(key)
            problem_at = _as_aware(problem.created_at)
            recovery_at = _as_aware(event.created_at)
            if recovery_at < start_at or problem_at > end_at:
                continue
            clipped_start = max(problem_at, start_at)
            clipped_end = min(recovery_at, end_at)
            intervals.append(_interval_from_events(problem, clipped_start, clipped_end, recovery_at, False))

    for key, problem in open_problem.items():
        problem_at = _as_aware(problem.created_at)
        if problem_at > end_at:
            continue
        clipped_start = max(problem_at, start_at)
        clipped_end = end_at
        latest = latest_event_by_key.get(key, problem)
        intervals.append(_interval_from_events(latest, clipped_start, clipped_end, None, True))

    return sorted(intervals, key=lambda item: (item.source_name, item.folder_name, item.problem_at))


def _interval_from_events(
    event: RecordStatusEvent,
    problem_at: datetime,
    end_at: datetime,
    recovery_at: datetime | None,
    active: bool,
) -> DowntimeInterval:
    duration = max(0, int((end_at - problem_at).total_seconds()))
    return DowntimeInterval(
        source_id=event.source_id,
        source_name=event.source.name if event.source else event.source_id,
        nvr_name=event.source.nvr.hostname if event.source and event.source.nvr else None,
        folder_name=event.folder_name,
        camera_name=event.camera.hostname if event.camera else None,
        problem_at=problem_at,
        recovery_at=recovery_at,
        duration_seconds=duration,
        active=active,
    )


def build_source_daily_report(db: Session, source: RecordSource, start_at: datetime, end_at: datetime) -> SourceDailyReport:
    intervals = get_record_downtime_intervals(db, start_at, end_at, source.id)
    status_rows = db.query(RecordFolderStatus.status).filter(RecordFolderStatus.source_id == source.id).all()
    status_counts = defaultdict(int)
    for (status,) in status_rows:
        status_counts[status] += 1

    total_count = sum(status_counts.values())
    active_problem_count = status_counts["stale"] + status_counts["missing"]
    inactive_count = status_counts["long_dead"] + status_counts["unknown"]
    normal_count = max(0, status_counts["healthy"])

    return SourceDailyReport(
        source=source,
        report_start=start_at,
        report_end=end_at,
        intervals=intervals,
        normal_count=normal_count,
        active_problem_count=active_problem_count,
        inactive_count=inactive_count,
        total_count=total_count,
        trend_rows=build_14_day_trend(db, source.id, as_of=end_at),
    )


def build_14_day_trend(
    db: Session,
    source_id: str,
    as_of: datetime | None = None,
) -> list[dict]:
    """Build downtime totals from available check data through ``as_of``.

    A new source has no evidence of its state before its first persisted folder
    check.  Therefore its trend begins at that check time, rather than at
    midnight (or 14 days earlier).  The current day is likewise capped at the
    report generation time so active downtime is never projected into the
    future.
    """
    tz = _tz()
    as_of = _as_aware(as_of or datetime.now(timezone.utc))
    as_of_local = as_of.astimezone(tz)
    today = as_of_local.date()
    first_check_at = (
        db.query(func.min(RecordFolderCheck.checked_at))
        .filter(RecordFolderCheck.source_id == source_id)
        .scalar()
    )
    if first_check_at is None:
        return []

    requested_start = tz.localize(datetime.combine(today - timedelta(days=13), time.min)).astimezone(
        timezone.utc
    )
    available_start = _as_aware(first_check_at)
    trend_start = max(requested_start, available_start)
    channel_days: dict[tuple[str, str | None, str], dict] = {}
    rows = []
    for days_back in range(13, -1, -1):
        day = today - timedelta(days=days_back)
        day_start = tz.localize(datetime.combine(day, time.min)).astimezone(timezone.utc)
        day_end = tz.localize(datetime.combine(day, time.max)).astimezone(timezone.utc)
        start = max(day_start, trend_start)
        end = min(day_end, as_of)
        if start > end:
            continue
        intervals = get_record_downtime_intervals(db, start, end, source_id)
        for item in intervals:
            key = (item.folder_name, item.camera_name, day.isoformat())
            row = channel_days.setdefault(
                key,
                {
                    "date": day.isoformat(),
                    "folder_name": item.folder_name,
                    "camera_name": item.camera_name,
                    "channel": _channel_label(item.folder_name, item.camera_name),
                    "incident_count": 0,
                    "downtime_minutes": 0.0,
                    "active_count": 0,
                },
            )
            row["incident_count"] += 1
            row["downtime_minutes"] = round(row["downtime_minutes"] + (item.duration_seconds / 60), 2)
            row["active_count"] += 1 if item.active else 0
    rows.extend(channel_days.values())
    return rows


def summarize_downtime_by_channel(
    db: Session,
    start_at: datetime,
    end_at: datetime,
    source_id: str | None = None,
) -> list[dict]:
    intervals = get_record_downtime_intervals(db, start_at, end_at, source_id)
    statuses = (
        db.query(RecordFolderStatus)
        .options(joinedload(RecordFolderStatus.source).joinedload(RecordSource.nvr), joinedload(RecordFolderStatus.camera))
    )
    if source_id:
        statuses = statuses.filter(RecordFolderStatus.source_id == source_id)

    grouped: dict[tuple[str, str], dict] = {}
    for status_row in statuses.all():
        key = (status_row.source_id, status_row.folder_name)
        grouped[key] = {
            "source_id": status_row.source_id,
            "source_name": status_row.source.name if status_row.source else None,
            "nvr_name": status_row.source.nvr.hostname if status_row.source and status_row.source.nvr else None,
            "folder_name": status_row.folder_name,
            "channel_name": status_row.folder_name,
            "camera_name": status_row.camera.hostname if status_row.camera else None,
            "status": status_row.status,
            "last_mtime": _as_aware(status_row.last_mtime).isoformat() if status_row.last_mtime else None,
            "last_checked_at": _as_aware(status_row.last_checked_at).isoformat() if status_row.last_checked_at else None,
            "incident_count": 0,
            "active_incident_count": 0,
            "downtime_seconds": 0,
            "downtime_minutes": 0,
            "history_downtime": "",
        }

    histories: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in intervals:
        key = (item.source_id, item.folder_name)
        row = grouped.setdefault(
            key,
            {
                "source_id": item.source_id,
                "source_name": item.source_name,
                "nvr_name": item.nvr_name,
                "folder_name": item.folder_name,
                "channel_name": item.folder_name,
                "camera_name": item.camera_name,
                "status": "unknown",
                "last_mtime": None,
                "last_checked_at": None,
                "incident_count": 0,
                "active_incident_count": 0,
                "downtime_seconds": 0,
                "downtime_minutes": 0,
                "history_downtime": "",
            },
        )
        row["incident_count"] += 1
        row["active_incident_count"] += 1 if item.active else 0
        row["downtime_seconds"] += item.duration_seconds
        row["downtime_minutes"] = round(row["downtime_seconds"] / 60, 2)
        if not row.get("camera_name") and item.camera_name:
            row["camera_name"] = item.camera_name
        recovery = _fmt_event_time(item.recovery_at) if item.recovery_at else "Still active"
        histories[key].append(
            f"{_fmt_event_time(item.problem_at)} -> {recovery} ({_duration_label(item.duration_seconds)})"
        )

    for key, row in grouped.items():
        row["history_downtime"] = "; ".join(histories.get(key, []))

    return sorted(grouped.values(), key=lambda item: (item.get("source_name") or "", item["folder_name"]))


def format_daily_report_message(report: SourceDailyReport) -> str:
    nvr_name = report.source.nvr.hostname if report.source.nvr else report.source.name
    grouped: dict[str, list[DowntimeInterval]] = defaultdict(list)
    for item in report.intervals:
        grouped[item.folder_name].append(item)

    lines = [
        f"📊 Daily Report NVR {nvr_name}",
        f"📅 {_fmt_report_date(datetime.now(timezone.utc), include_timezone=True)}",
        "",
        "",
    ]

    if not grouped:
        lines.append("✅ No record downtime detected in this report window.")
        lines.append("")

    for folder_name in sorted(grouped):
        lines.append(f"📷 {folder_name}")
        for item in grouped[folder_name]:
            lines.append(f"  🔴 Problem: {_fmt_event_time(item.problem_at)}")
            if item.recovery_at:
                lines.append(f"  ✅ Recovery: {_fmt_event_time(item.recovery_at)}")
            else:
                lines.append("  ✅ Recovery: Still active")
            lines.append(f"  ⏱️ Duration: {_duration_label(item.duration_seconds)}")
        lines.append("")

    lines.extend(
        [
            "──────────────────",
            f"✅ Normal: {report.normal_count} channel",
            f"🔴 Active Problems: {report.active_problem_count} channel",
            f"⚫ Inactive: {report.inactive_count} channel",
            f"📷 Total: {report.total_count} channel",
        ]
    )
    return "\n".join(lines)


def generate_trend_pdf(report: SourceDailyReport) -> Path:
    reports_dir = Path("static") / "reports" / "record_checks"
    reports_dir.mkdir(parents=True, exist_ok=True)
    source_slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in report.source.name)
    filename = f"record-check-trend-{source_slug}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.pdf"
    path = reports_dir / filename

    doc = SimpleDocTemplate(
        str(path),
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=28,
    )
    styles = getSampleStyleSheet()
    normal_style = ParagraphStyle(
        "RecordTrendBody",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#111827"),
    )
    header_style = ParagraphStyle(
        "RecordTrendHeader",
        parent=styles["BodyText"],
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=1,
    )
    elements = [
        Paragraph(f"Record Check 14-Day Trend - {report.source.name}", styles["Title"]),
        Spacer(1, 12),
    ]
    trend_image = generate_trend_image(report)
    elements.append(RLImage(str(trend_image), width=720, height=405))
    elements.append(Spacer(1, 12))

    channel_rows: dict[str, dict] = {}
    for row in report.trend_rows:
        channel = row["channel"]
        aggregate = channel_rows.setdefault(
            channel,
            {
                "channel": channel,
                "incident_count": 0,
                "downtime_minutes": 0.0,
                "active_count": 0,
                "history": [],
            },
        )
        aggregate["incident_count"] += int(row["incident_count"])
        aggregate["downtime_minutes"] = round(aggregate["downtime_minutes"] + float(row["downtime_minutes"]), 2)
        aggregate["active_count"] += int(row["active_count"])

    for item in report.intervals:
        channel = _channel_label(item.folder_name, item.camera_name)
        aggregate = channel_rows.setdefault(
            channel,
            {
                "channel": channel,
                "incident_count": 0,
                "downtime_minutes": 0.0,
                "active_count": 0,
                "history": [],
            },
        )
        recovery = _fmt_event_time(item.recovery_at) if item.recovery_at else "Still active"
        aggregate["history"].append(
            f"{_fmt_event_time(item.problem_at)} -> {recovery} ({_duration_label(item.duration_seconds)})"
        )

    data = [
        [
            Paragraph("Channel", header_style),
            Paragraph("Incidents", header_style),
            Paragraph("Downtime<br/>Minutes", header_style),
            Paragraph("Active", header_style),
            Paragraph("Historical Downtime", header_style),
        ]
    ]
    top_rows = sorted(channel_rows.values(), key=lambda item: item["downtime_minutes"], reverse=True)[:30]
    if not top_rows:
        top_rows = [{"channel": "-", "incident_count": 0, "downtime_minutes": 0, "active_count": 0, "history": []}]
    for row in top_rows:
        data.append(
            [
                Paragraph(str(row["channel"]), normal_style),
                Paragraph(str(row["incident_count"]), normal_style),
                Paragraph(str(row["downtime_minutes"]), normal_style),
                Paragraph(str(row["active_count"]), normal_style),
                Paragraph("<br/>".join(row["history"][:5]) if row["history"] else "-", normal_style),
            ]
        )

    table = Table(data, colWidths=[160, 65, 80, 55, 440], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
                ("ALIGN", (1, 1), (3, -1), "RIGHT"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)
    return path


def generate_trend_image(report: SourceDailyReport) -> Path:
    reports_dir = Path("static") / "reports" / "record_checks"
    reports_dir.mkdir(parents=True, exist_ok=True)
    source_slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in report.source.name)
    filename = f"record-check-trend-{source_slug}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.png"
    path = reports_dir / filename

    width, height = 1100, 620
    margin = 70
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arial.ttf", 28)
        label_font = ImageFont.truetype("arial.ttf", 16)
        small_font = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        title_font = label_font = small_font = ImageFont.load_default()

    draw.text((margin, 28), f"Record Check 14-Day Trend per Channel - {report.source.name}", fill="#111827", font=title_font)
    plot_left, plot_top = margin, 110
    plot_right, plot_bottom = width - margin, height - 95
    draw.rectangle((plot_left, plot_top, plot_right, plot_bottom), outline="#d1d5db", width=1)

    channel_totals: dict[str, float] = defaultdict(float)
    for row in report.trend_rows:
        channel_totals[row["channel"]] += float(row["downtime_minutes"])
    top_channels = sorted(channel_totals.items(), key=lambda item: item[1], reverse=True)[:12]
    values = [value for _, value in top_channels]
    max_value = max(values) if values else 0
    scale_max = max(max_value, 10)
    bar_gap = 10
    bar_width = max(16, int((plot_right - plot_left - (len(values) - 1) * bar_gap) / max(len(values), 1)))

    for idx, (channel, value) in enumerate(top_channels):
        x0 = plot_left + idx * (bar_width + bar_gap)
        bar_height = int((value / scale_max) * (plot_bottom - plot_top - 20)) if scale_max else 0
        y0 = plot_bottom - bar_height
        x1 = x0 + bar_width
        color = "#dc2626" if value else "#9ca3af"
        draw.rectangle((x0, y0, x1, plot_bottom), fill=color)
        label = channel[:16]
        draw.text((x0, plot_bottom + 8), label, fill="#374151", font=small_font)
        if value:
            draw.text((x0, y0 - 18), str(round(value, 1)), fill="#111827", font=small_font)

    if not top_channels:
        draw.text((plot_left + 20, plot_top + 30), "No downtime in the last 14 days", fill="#374151", font=label_font)

    draw.text((margin, height - 48), "Total downtime minutes by channel, last 14 days", fill="#374151", font=label_font)
    draw.text((width - 310, height - 48), f"Generated: {_fmt_report_date(datetime.now(timezone.utc))}", fill="#6b7280", font=small_font)
    image.save(path)
    return path


def send_record_check_daily_reports(db: Session) -> dict:
    if get_config("record_check_daily_report_enabled", "1") != "1":
        return {"records_processed": 0, "reason": "record_check_daily_report_enabled disabled"}

    service = WAGatewayService(db)
    if not service.config.is_configured() or not service.config.default_receiver:
        return {"records_processed": 0, "reason": "GoWA not configured"}

    start_at, end_at = get_report_window(days_back=0)
    sources = (
        db.query(RecordSource)
        .options(joinedload(RecordSource.nvr))
        .filter(RecordSource.enabled.is_(True))
        .order_by(RecordSource.name.asc())
        .all()
    )
    receivers = [item.strip() for item in service.config.default_receiver.split(",") if item.strip()]
    sent = 0
    errors = []

    for source in sources:
        report = build_source_daily_report(db, source, start_at, end_at)
        message = format_daily_report_message(report)
        pdf_path = generate_trend_pdf(report)

        for receiver in receivers:
            phone = receiver if "@" in receiver else format_phone_number(receiver)
            result = service.send_text(phone, message)
            if result.get("success"):
                sent += 1
            else:
                errors.append(str(result.get("error", "unknown error")))
            file_result = service.send_file(
                phone,
                str(pdf_path),
                caption=f"14-day record-check trend PDF - {source.name}",
            )
            if not file_result.get("success"):
                errors.append(str(file_result.get("error", "unknown file error")))

    return {"records_processed": sent, "sources": len(sources), "errors": errors[:5]}
