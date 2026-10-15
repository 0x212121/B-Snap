"""Record alerts use isolated tables and mocked GoWA HTTP delivery."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from requests import HTTPError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.models.record_check import RecordSource, RecordStatusEvent
from app.utils import record_check
from app.utils.wa_gateway import GoWAConfig, WAGatewayService


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema SQLite setup; unrelated models require JSONB."""


@pytest.fixture
def record_db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    tables = {
        Base.metadata.tables[name]
        for name in (
            "record_sources",
            "record_check_runs",
            "record_folder_checks",
            "record_folder_statuses",
            "record_status_events",
        )
    }
    pending = list(tables)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in tables:
                tables.add(fk.column.table)
                pending.append(fk.column.table)
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table in tables]
    )
    try:
        with Session(engine) as db:
            yield db
    finally:
        engine.dispose()


@pytest.mark.parametrize(("source_count", "problem_count"), [(1, 1), (4, 1), (4, 4)])
@pytest.mark.parametrize("delivery_succeeds", [True, False])
def test_alert_and_recovery_delivery(
    record_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    source_count: int,
    problem_count: int,
    delivery_succeeds: bool,
) -> None:
    group = "120363000000000001@g.us"
    config = {
        "gowa_enabled": "1",
        "gowa_base_url": "http://gowa.invalid",
        "gowa_default_receiver": f" {group}, , 0812-3456-7890, 628111111111@s.whatsapp.net ",
    }
    monkeypatch.setattr(
        GoWAConfig, "get", lambda _self, key, default=None: config.get(key, default)
    )
    response = MagicMock()
    response.json.return_value = {"code": "SUCCESS", "results": {}}
    if not delivery_succeeds:
        response.raise_for_status.side_effect = HTTPError("Gateway unavailable")
    post = MagicMock(return_value=response)
    monkeypatch.setattr(WAGatewayService, "_post_with_retry", post)
    monkeypatch.setattr(record_check, "reconcile_mounts", lambda _active: None)

    for index in range(source_count):
        record_db.add(RecordSource(name=f"Source {index}", base_path=f"/records/{index}"))
    record_db.commit()

    scan_status = "stale"

    def scan(_db: Session, source: RecordSource, checked_at: datetime) -> list:
        status = scan_status if int(source.name.split()[-1]) < problem_count else "healthy"
        return [
            record_check.FolderScanResult(
                folder_name="ch01",
                folder_path=f"{source.base_path}/ch01",
                last_mtime=checked_at,
                age_seconds=5000 if status == "stale" else 0,
                status=status,
            )
        ]

    monkeypatch.setattr(record_check, "_scan_record_source", scan)
    for event_type in ("record_alert", "record_recovery"):
        summary = record_check.run_all_record_checks(record_db)

        assert summary["sources_checked"] == source_count
        assert summary["sources_failed"] == 0
        assert post.call_count == problem_count * 3
        payloads = [call.kwargs["json"] for call in post.call_args_list]
        assert [payload["phone"] for payload in payloads] == [
            group,
            "6281234567890@s.whatsapp.net",
            "628111111111@s.whatsapp.net",
        ] * problem_count
        for index in range(problem_count):
            message = payloads[index * 3]["message"]
            assert f"Source: Source {index}\n" in message
            assert "ch01 - Unmapped" in message
            assert ("ALERT" if event_type == "record_alert" else "RECOVERY") in message

        events = (
            record_db.query(RecordStatusEvent)
            .filter(RecordStatusEvent.event_type == event_type)
            .all()
        )
        assert len(events) == problem_count
        assert all(event.notification_sent == delivery_succeeds for event in events)
        assert all(bool(event.notification_error) != delivery_succeeds for event in events)
        scan_status = "healthy"
        post.reset_mock()
