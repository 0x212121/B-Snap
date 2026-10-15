"""Regression coverage for chart images in downloaded Insights PDFs."""

from __future__ import annotations

import base64

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest

from PIL import Image

from app.routes import insights
from app.utils.insights_report_locale import InsightsReportLocale


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; all report queries are mocked."""


@pytest.mark.parametrize(
    ("language", "expected"), [("en", "1,234.50"), ("id", "1.234,50"), ("fr", "1,234.50")]
)
def test_report_number_format(language: str, expected: str) -> None:
    """Localize decimal values and fall back to English for unsupported languages."""
    assert InsightsReportLocale(language).number(1234.5, 2) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "id"])
@pytest.mark.parametrize(
    ("zone", "zone_label"),
    [("Asia/Jakarta", "WIB"), ("Asia/Makassar", "WITA"), ("Asia/Jayapura", "WIT")],
)
@pytest.mark.parametrize(
    "chart_field",
    ["api_chart", "email_chart", "topcmd_chart", "availability_chart", "incidents_chart"],
)
async def test_report_embeds_chart_image(
    *,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    chart_field: str,
    language: str,
    zone: str,
    zone_label: str,
) -> None:
    """Build the actual PDF and require an embedded image, not merely HTTP 200."""
    image = BytesIO()
    Image.new("RGB", (100, 50), "blue").save(image, format="PNG")
    form = {
        "language": language,
        "start_date": "2026-10-01",
        "end_date": "2026-10-15",
        chart_field: "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
    }
    request = MagicMock()
    request.form = AsyncMock(return_value=form)
    monkeypatch.setattr(insights, "get_current_timezone", lambda _db: zone)
    monkeypatch.setattr(insights, "_total_count", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(insights, "get_top_commands", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(insights, "get_top_cameras_by_email", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(insights, "LOGO_PATH", "missing-report-test-logo.png")
    report_text = []
    paragraph = insights.Paragraph

    def capture_paragraph(*args, **kwargs):
        result = paragraph(*args, **kwargs)
        report_text.append(result.getPlainText())
        return result

    monkeypatch.setattr(insights, "Paragraph", capture_paragraph)
    monkeypatch.setattr(
        insights,
        "get_camera_operations_data",
        AsyncMock(
            return_value={
                "total_uptime_seconds": 3600,
                "total_downtime_seconds": 0,
                "total_offline_incidents": 0,
                "top_downtime_cameras": [],
            }
        ),
    )

    response = await insights.get_executive_report_pro(
        request, db=MagicMock(), start_date=None, end_date=None, current_admin=MagicMock()
    )
    pdf = b"".join([chunk async for chunk in response.body_iterator])

    assert response.media_type == "application/pdf"
    assert pdf.startswith(b"%PDF-")
    assert b"/Subtype /Image" in pdf
    assert "Chart embedding failed" not in caplog.text
    text = "\n".join(report_text)
    if language == "id":
        assert "Laporan Eksekutif B-SNAP" in text
        assert f"Periode: 01 Okt 2026 - 15 Okt 2026 ({zone_label})" in text
        assert "1.234 panggilan API" in text
        assert "Ringkasan Operasional Kamera" in text
        assert "Indeks tren aktivitas adalah 50/100 (stabil)" in text
        assert "Executive Summary" not in text
    else:
        assert "B-SNAP Executive Report" in text
        assert f"Period: Oct 01, 2026 - Oct 15, 2026 ({zone_label})" in text
        assert "1,234 API calls" in text
        assert "Camera Operations Summary" in text
        assert "activity trend index is 50/100 (stable)" in text
        assert "Ringkasan Eksekutif" not in text
    assert "2026-10-01_2026-10-15.pdf" in response.headers["content-disposition"]
