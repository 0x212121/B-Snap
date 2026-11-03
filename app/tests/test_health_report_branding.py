"""Check branding in generated health reports without application data."""

from datetime import date

import pytest

from app.routes import health


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full SQLite schema setup; reports use in-memory inputs only."""


@pytest.mark.asyncio
async def test_csv_brand_does_not_create_a_spreadsheet_formula():
    response = health._generate_csv_report(
        date(2026, 10, 1),
        date(2026, 10, 2),
        [],
        {},
        100.0,
        None,
        None,
        0,
        99.5,
        brand_name="=Acme",
    )
    text = b"".join([chunk async for chunk in response.body_iterator]).decode()
    assert text.startswith("'=Acme Health Report")


@pytest.mark.asyncio
async def test_pdf_brand_is_escaped(monkeypatch):
    paragraphs = []
    original = health.Paragraph

    def capture(*args, **kwargs):
        item = original(*args, **kwargs)
        paragraphs.append(item.getPlainText())
        return item

    monkeypatch.setattr(health, "Paragraph", capture)
    response = health._generate_pdf_report(
        date(2026, 10, 1),
        date(2026, 10, 2),
        [],
        {},
        100.0,
        None,
        None,
        0,
        99.5,
        include_charts=False,
        brand_name="Acme <CCTV>",
    )
    pdf = b"".join([chunk async for chunk in response.body_iterator])
    assert pdf.startswith(b"%PDF-")
    assert "Acme <CCTV> Health Report" in paragraphs
