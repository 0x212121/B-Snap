"""Consolidated reports with mocked delivery and isolated historical checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytz

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.jobs import scheduler as worker
from app.models.camera import Camera
from app.models.nvr import NVR
from app.models.record_check import RecordFolderCheck, RecordSource, RecordStatusEvent
from app.routes import jobs as job_routes
from app.utils import record_check_report as reports
from app.utils.wa_gateway import WAGatewayService


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; use mocks or one compatible table."""


@pytest.fixture
def window():
    return datetime(2026, 10, 7, 16, tzinfo=UTC), datetime(2026, 10, 8, 16, tzinfo=UTC)


def make_report(source, window):
    return reports.SourceDailyReport(source, *window, [], 14, 2, 0, 16, [])


@pytest.mark.parametrize(
    ("zone", "hour"), [("Asia/Jakarta", 17), ("Asia/Makassar", 16), ("Asia/Jayapura", 15)]
)
@pytest.mark.parametrize("local_hour", [0, 8, 23])
def test_previous_day_midnight_boundaries(monkeypatch, zone, hour, local_hour):
    tz = pytz.timezone(zone)
    now = tz.localize(datetime(2026, 10, 9, local_hour, 1))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(reports, "datetime", Clock)
    monkeypatch.setattr(reports, "_tz", lambda: tz)
    start, end = reports.get_report_window(days_back=1)
    assert start == datetime(2026, 10, 7, hour, tzinfo=UTC)
    assert end == datetime(2026, 10, 8, hour, tzinfo=UTC)
    assert end - start == timedelta(days=1)


def test_historical_counts_ignore_today(monkeypatch, window):
    engine = create_engine("sqlite://")
    RecordFolderCheck.__table__.create(engine)
    source = SimpleNamespace(id="source-a")
    start, end = window
    monkeypatch.setattr(reports, "get_record_downtime_intervals", lambda *_args: [])
    trend = MagicMock(return_value=[])
    monkeypatch.setattr(reports, "build_14_day_trend", trend)
    with Session(engine) as db:
        for index, (folder, status, timestamp) in enumerate(
            [
                ("ch01", "stale", start),
                ("ch01", "healthy", end - timedelta(seconds=1)),
                ("ch01", "missing", end),
                ("ch02", "long_dead", start),
                ("ch03", "missing", end - timedelta(hours=1)),
            ]
        ):
            db.add(
                RecordFolderCheck(
                    id=str(index),
                    source_id=source.id,
                    run_id="run",
                    folder_name=folder,
                    folder_path="/test/" + folder,
                    status=status,
                    checked_at=timestamp,
                )
            )
        db.commit()
        report = reports.build_source_daily_report(db, source, start, end)
        assert (
            report.normal_count,
            report.active_problem_count,
            report.inactive_count,
            report.total_count,
        ) == (1, 1, 1, 3)
        assert trend.call_args.kwargs["as_of"] == end
        assert trend.call_args.kwargs["end_exclusive"] is True
    engine.dispose()


@pytest.fixture
def delivery(monkeypatch, window):
    db = MagicMock()
    sources = [
        SimpleNamespace(id=str(i), name=f"Source {i}", nvr=SimpleNamespace(hostname=f"NVR {i}"))
        for i in range(1, 6)
    ]
    db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = (
        sources
    )
    service = MagicMock()
    service.config.default_receiver = "report@g.us,report@g.us,second@g.us"
    service.send_text.return_value = service.send_file.return_value = {"success": True}
    monkeypatch.setattr(reports, "get_config", lambda _key, default: default)
    monkeypatch.setattr(reports, "WAGatewayService", lambda _db: service)
    window_getter = MagicMock(return_value=window)
    monkeypatch.setattr(reports, "get_report_window", window_getter)
    builder = MagicMock(side_effect=lambda _db, source, *_args: make_report(source, window))
    monkeypatch.setattr(reports, "build_source_daily_report", builder)
    pdf = MagicMock(return_value=Path("combined.pdf"))
    monkeypatch.setattr(reports, "generate_combined_trend_pdf", pdf)
    return db, sources, service, builder, pdf, window_getter


def test_five_nvrs_send_one_summary_and_pdf_per_recipient(delivery):
    db, sources, service, builder, pdf, window_getter = delivery
    result = reports.send_record_check_daily_reports(db)
    window_getter.assert_called_once_with(days_back=1)
    assert builder.call_count == 5
    assert pdf.call_count == 1
    assert len(pdf.call_args.args[0]) == 5
    assert service.send_text.call_count == service.send_file.call_count == 2
    message = service.send_text.call_args.args[1]
    assert all(source.nvr.hostname in message for source in sources)
    assert "Oct 8, 2026 00:00 - 24:00 WITA" in message
    assert result["sources_reported"] == 5
    assert result["errors"] == []


def test_source_failure_does_not_stop_other_nvrs(delivery, window):
    db, _, service, builder, pdf, _ = delivery

    def build(db, source, *args):
        if source.id == "2":
            raise RuntimeError("private error")
        return make_report(source, window)

    builder.side_effect = build
    result = reports.send_record_check_daily_reports(db)
    assert builder.call_count == 5
    assert result["sources_reported"] == 4
    assert result["failed_sources"] == ["Source 2"]
    assert "Report unavailable: Source 2" in service.send_text.call_args.args[1]
    assert "private error" not in service.send_text.call_args.args[1]
    assert len(pdf.call_args.args[0]) == 4


def test_failed_upload_does_not_repeat_text_or_block_next_recipient(delivery):
    db, _, service, _, _, _ = delivery
    service.send_file.side_effect = [RuntimeError("upload failed"), {"success": True}]
    result = reports.send_record_check_daily_reports(db)
    assert service.send_text.call_count == 2
    assert service.send_file.call_count == 2
    assert result["files_sent"] == 1
    assert result["errors"]


def test_pdf_failure_still_sends_summary(delivery):
    db, _, service, _, pdf, _ = delivery
    pdf.side_effect = RuntimeError("render failed")
    result = reports.send_record_check_daily_reports(db)
    assert "PDF unavailable" in service.send_text.call_args.args[1]
    service.send_file.assert_not_called()
    assert result["errors"]


def test_empty_sources_do_not_send(delivery):
    db, _, service, _, pdf, _ = delivery
    db.query.return_value.options.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )
    assert reports.send_record_check_daily_reports(db)["sources"] == 0
    pdf.assert_not_called()
    service.send_text.assert_not_called()


def test_pdf_retry_rewinds_payload(monkeypatch, tmp_path):
    service = WAGatewayService.__new__(WAGatewayService)
    service.config = SimpleNamespace(is_configured=lambda: True, base_url="http://gowa-test")
    service.session = MagicMock()
    service.send_timeout_seconds, service.send_retries = 10, 1
    payloads = []

    def post(url, **kwargs):
        payloads.append(kwargs["files"]["file"][1].read())
        response = MagicMock(status_code=500 if len(payloads) == 1 else 200)
        response.json.return_value = {}
        return response

    service.session.post.side_effect = post
    monkeypatch.setattr("app.utils.wa_gateway.time.sleep", lambda _: None)
    path = tmp_path / "report.pdf"
    path.write_bytes(b"PDF contents")
    assert service.send_file("report@g.us", str(path))["success"]
    assert payloads == [b"PDF contents", b"PDF contents"]


def test_midnight_events_are_excluded_and_full_day_downtime_is_exact(window):
    engine = create_engine("sqlite://")
    for model in (Camera, NVR, RecordSource, RecordStatusEvent):
        model.__table__.create(engine)
    start, end = window
    with Session(engine) as db:
        db.add_all(
            [
                RecordStatusEvent(
                    id="1",
                    source_id="a",
                    folder_status_id="status",
                    folder_name="ch01",
                    event_type="record_alert",
                    new_status="stale",
                    created_at=start - timedelta(hours=1),
                ),
                RecordStatusEvent(
                    id="2",
                    source_id="a",
                    folder_status_id="status",
                    folder_name="ch01",
                    event_type="record_recovery",
                    new_status="healthy",
                    created_at=end,
                ),
                RecordStatusEvent(
                    id="3",
                    source_id="a",
                    folder_status_id="status",
                    folder_name="ch02",
                    event_type="record_alert",
                    new_status="missing",
                    created_at=end,
                ),
            ]
        )
        db.commit()
        intervals = reports.get_record_downtime_intervals(db, start, end, "a")
        assert len(intervals) == 1
        assert intervals[0].duration_seconds == 86400
        assert intervals[0].problem_at == start
        assert intervals[0].active
        assert intervals[0].recovery_at is None
    engine.dispose()


@pytest.mark.parametrize(("language", "date"), [("en", "Oct 8, 2026"), ("id", "8 Okt 2026")])
def test_report_language_changes_date_without_changing_clock(monkeypatch, window, language, date):
    monkeypatch.setattr(reports, "_tz", lambda: pytz.timezone("Asia/Makassar"))
    assert reports._report_period(*window, language=language) == f"{date} 00:00 - 24:00 WITA"


def test_combined_pdf_contains_all_sources_and_escapes_names(monkeypatch, tmp_path, window):
    pymupdf = pytest.importorskip("pymupdf")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reports, "_tz", lambda: pytz.timezone("Asia/Makassar"))
    items = [
        make_report(
            SimpleNamespace(
                name=f"Source {i} & <Storage>", nvr=SimpleNamespace(hostname=f"NVR {i}")
            ),
            window,
        )
        for i in range(1, 6)
    ]
    path = reports.generate_combined_trend_pdf(items, *window, ["Source unavailable"])
    reader = pymupdf.open(path)
    text = "\n".join(page.get_text() for page in reader)
    assert all(f"NVR {i}" in text and f"Source {i} & <Storage>" in text for i in range(1, 6))
    assert "Source unavailable" in text
    assert "Oct 8, 2026 00:00 - 24:00 WITA" in text


def test_trend_ends_on_report_date_without_losing_midnight_second(monkeypatch, window):
    start, end = window
    monkeypatch.setattr(reports, "_tz", lambda: pytz.timezone("Asia/Makassar"))
    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = start - timedelta(days=20)
    intervals = MagicMock(return_value=[])
    monkeypatch.setattr(reports, "get_record_downtime_intervals", intervals)
    reports.build_14_day_trend(db, "a", as_of=end, end_exclusive=True)
    assert intervals.call_count == 14
    assert intervals.call_args.args[1:3] == (start, end)
    assert intervals.call_args_list[0].args[1] == start - timedelta(days=13)


def test_worker_stores_partial_report_details_in_job_history(monkeypatch):
    db, log = MagicMock(), MagicMock()
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    monkeypatch.setattr(worker.JobExecutionLog, "start_execution", lambda *_args: log)
    result = {
        "records_processed": 1,
        "status": "partial",
        "error_message": "Source unavailable",
        "failed_sources": ["Source 2"],
    }

    @worker.logged_job("record_check_daily_report", "Record Check Daily Report")
    def run():
        return result

    assert run() == result
    log.complete.assert_called_once_with(
        db, status="partial", records=1, metadata=result, error="Source unavailable"
    )
    db.close.assert_called_once()


@pytest.mark.asyncio
async def test_manual_run_passes_language_and_stores_partial_status(monkeypatch):
    db, log = MagicMock(), MagicMock()
    monkeypatch.setattr(
        job_routes, "get_job_from_db", lambda _job_id: {"id": "record_check_daily_report"}
    )
    monkeypatch.setattr(job_routes.JobExecutionLog, "start_execution", lambda *_args: log)
    sender = MagicMock(
        return_value={"records_processed": 1, "status": "partial", "error_message": "PDF failed"}
    )
    monkeypatch.setattr(job_routes, "send_record_check_daily_reports", sender)
    response = await job_routes.run_job_now(
        "record_check_daily_report", db=db, current_admin=MagicMock(), language="id"
    )
    assert response.status_code == 200
    sender.assert_called_once_with(db, language="id")
    assert log.complete.call_args.args[1] == "partial"
    assert log.complete.call_args.kwargs["error"] == "PDF failed"
