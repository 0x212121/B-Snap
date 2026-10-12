"""GoWA monitoring uses only a local Configuration table and mocked transports."""

from __future__ import annotations

import json

from email.parser import Parser
from unittest.mock import Mock

import pytest

from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.config import Configuration
from app.routes import config as config_routes
from app.utils import email_helper, wa_monitor as monitor
from app.utils.wa_gateway import WAGatewayService


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the full-schema fixture; PostgreSQL models are not needed here."""


@pytest.fixture
def local_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Configuration.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(
            Configuration(key=key, value=value)
            for key, value in {
                "gowa_enabled": "1",
                "gowa_monitor_enabled": "1",
                "gowa_monitor_recipients": "admin@example.com, ops@example.com",
                "gowa_base_url": "http://gowa-test",
                "gowa_device_id": "test-device",
                "gowa_monitor_state": "{}",
                "timezone": "Asia/Makassar",
            }.items()
        )
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def transports(monkeypatch):
    status = Mock(return_value={"connected": False, "logged_in": True})
    email = Mock()
    monkeypatch.setattr(WAGatewayService, "check_connection", status)
    monkeypatch.setattr(monitor, "send_email", email)
    return status, email


def saved_state(db: Session) -> dict:
    return json.loads(db.get(Configuration, monitor.STATE_KEY).value)


def test_confirm_deduplicate_across_sessions_and_rearm(local_db, transports) -> None:
    status, email = transports
    monitor.check_gowa_device(local_db)
    email.assert_not_called()
    incident_time = saved_state(local_db)["incident_time"]
    monitor.check_gowa_device(local_db)
    email.assert_called_once()
    assert email.call_args.kwargs == {"high_importance": True}
    assert saved_state(local_db)["incident_time"] == incident_time
    assert email.call_args.args[0] == ["admin@example.com", "ops@example.com"]
    # A fresh session simulates restarting the worker with persisted state.
    with Session(local_db.bind) as restarted:
        monitor.check_gowa_device(restarted)
    email.assert_called_once()
    # Switching from disconnected to logged off is still the same outage.
    status.return_value = {"connected": False, "logged_in": False}
    monitor.check_gowa_device(local_db)
    email.assert_called_once()
    status.return_value = {"connected": True, "logged_in": True}
    monitor.check_gowa_device(local_db)
    status.return_value = {"connected": False, "logged_in": False}
    monitor.check_gowa_device(local_db)
    monitor.check_gowa_device(local_db)
    assert email.call_count == 2
    assert "logged_out" in email.call_args.args[1]


def test_transient_failure_does_not_send(local_db, transports) -> None:
    status, email = transports
    monitor.check_gowa_device(local_db)
    status.return_value = {"connected": True, "logged_in": True}
    monitor.check_gowa_device(local_db)
    status.return_value = {"connected": False, "logged_in": True}
    monitor.check_gowa_device(local_db)
    email.assert_not_called()


def test_failed_email_retries_with_original_incident_time(local_db, transports) -> None:
    _, email = transports
    email.side_effect = RuntimeError("transport failure")
    monitor.check_gowa_device(local_db)
    incident_time = saved_state(local_db)["incident_time"]
    with pytest.raises(RuntimeError, match="pending retry"):
        monitor.check_gowa_device(local_db)
    assert not saved_state(local_db).get("sent")
    email.side_effect = None
    monitor.check_gowa_device(local_db)
    assert saved_state(local_db)["incident_time"] == incident_time
    assert saved_state(local_db)["sent"]
    assert email.call_args_list[0] == email.call_args_list[1]


@pytest.mark.parametrize("key", ["gowa_enabled", "gowa_monitor_enabled"])
def test_disabled_monitor_skips_network_and_resets(local_db, transports, key) -> None:
    status, email = transports
    local_db.get(Configuration, key).value = "0"
    local_db.commit()
    assert monitor.check_gowa_device(local_db) == {"status": "disabled"}
    status.assert_not_called()
    email.assert_not_called()
    assert saved_state(local_db) == {}


def test_changed_device_starts_new_confirmation(local_db, transports) -> None:
    _, email = transports
    monitor.check_gowa_device(local_db)
    local_db.get(Configuration, "gowa_device_id").value = "other-device"
    local_db.commit()
    monitor.check_gowa_device(local_db)
    email.assert_not_called()
    monitor.check_gowa_device(local_db)
    assert "other-device" in email.call_args.args[2]


@pytest.mark.parametrize(
    ("connection", "expected"),
    [
        ({"connected": True, "logged_in": True}, "healthy"),
        ({"connected": False, "logged_in": True}, "disconnected"),
        ({"connected": False, "logged_in": False}, "logged_out"),
        ({"connected": False, "error": "timeout"}, "unreachable"),
    ],
)
def test_device_status(connection, expected) -> None:
    assert monitor.device_status(connection) == expected


@pytest.mark.parametrize(
    ("zone", "expected_time"),
    [
        ("Asia/Jakarta", "06/10/2026 - 23:05:00 WIB"),
        ("Asia/Makassar", "07/10/2026 - 00:05:00 WITA"),
        ("Asia/Jayapura", "07/10/2026 - 01:05:00 WIT"),
    ],
)
def test_alert_time_uses_configured_timezone(local_db, transports, zone, expected_time) -> None:
    _, email = transports
    local_db.get(Configuration, "timezone").value = zone
    local_db.commit()
    monitor.check_gowa_device(local_db)
    state = saved_state(local_db)
    state["incident_time"] = "2026-10-06T16:05:00+00:00"
    local_db.get(Configuration, monitor.STATE_KEY).value = json.dumps(state)
    local_db.commit()
    monitor.check_gowa_device(local_db)
    assert expected_time in email.call_args.args[2]


def test_unreachable_alert_does_not_expose_transport_details(local_db, transports) -> None:
    status, email = transports
    status.return_value = {"connected": False, "error": "private-transport-detail"}
    monitor.check_gowa_device(local_db)
    monitor.check_gowa_device(local_db)
    assert "unreachable" in email.call_args.args[1]
    assert "private-transport-detail" not in email.call_args.args[2]


def test_recovery_cancels_failed_email_retry(local_db, transports) -> None:
    status, email = transports
    monitor.check_gowa_device(local_db)
    email.side_effect = RuntimeError("transport failure")
    with pytest.raises(RuntimeError, match="pending retry"):
        monitor.check_gowa_device(local_db)
    status.return_value = {"connected": True, "logged_in": True}
    monitor.check_gowa_device(local_db)
    assert email.call_count == 1
    assert "incident_time" not in saved_state(local_db)


@pytest.mark.parametrize(
    "value",
    ["invalid", "admin@example.com\r\nBcc: injected@example.com", "ok@example.com, invalid"],
)
def test_invalid_recipients_are_rejected(value) -> None:
    with pytest.raises(ValueError, match="Invalid GoWA alert email recipients"):
        monitor.parse_monitor_recipients(value)


def test_recipient_normalization() -> None:
    assert monitor.parse_monitor_recipients(" admin@example.com,admin@example.com, ") == [
        "admin@example.com"
    ]


@pytest.mark.parametrize(
    ("payload", "connected", "logged_in", "error"),
    [
        ({"is_connected": False, "is_logged_in": False}, False, False, None),
        ({"is_connected": False, "is_logged_in": True}, False, True, None),
        ({"is_connected": True}, True, True, None),
        ({"connected": True, "logged_in": True}, True, True, None),
        (
            {"is_connected": "false", "is_logged_in": "false"},
            False,
            None,
            "Invalid device status from GoWA",
        ),
        ({}, False, None, "Invalid device status from GoWA"),
    ],
)
def test_gateway_status_payload(local_db, payload, connected, logged_in, error) -> None:
    service = WAGatewayService(local_db)
    response = Mock(status_code=200, headers={"content-type": "application/json"})
    response.json.return_value = {"results": payload}
    service.session = Mock()
    service.session.get.return_value = response
    result = service.check_connection()
    assert result["connected"] is connected
    assert result.get("logged_in") is logged_in
    assert result["error"] == error
    service.session.get.assert_called_once_with("http://gowa-test/app/status", timeout=10)


@pytest.fixture
def settings_client(local_db, monkeypatch):
    def admin_override() -> Mock:
        return Mock()

    app = FastAPI()
    app.include_router(config_routes.router)
    app.dependency_overrides[config_routes.get_db] = lambda: local_db
    app.dependency_overrides[config_routes.admin_access_required] = admin_override
    monkeypatch.setattr(config_routes, "set_debug_mode", Mock())
    monkeypatch.setattr("app.core.logging_config.sanitize_loggers", lambda: None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def settings_form() -> dict:
    return {
        "snapshot_interval_minutes": "480",
        "healthcheck_interval_minutes": "15",
        "snapshot_concurrent_workers": "5",
        "max_screenshot_per_camera": "100",
        "snapshot_batch_size": "50",
        "snapshot_batch_delay_seconds": "5",
        "watermark_text": "B-Snap",
        "map_title": "Cameras",
        "timezone": "Asia/Makassar",
        "gowa_enabled": "1",
        "gowa_base_url": "http://gowa-test",
        "gowa_device_id": "test-device",
        "gowa_default_receiver": "628123456789",
        "gowa_monitor_enabled": "1",
        "gowa_monitor_recipients": "admin@example.com, ops@example.com",
        "gowa_monitor_interval_minutes": "3",
    }


def test_settings_save_and_disable(settings_client, settings_form, local_db) -> None:
    response = settings_client.post("/config/save", data=settings_form)
    assert response.status_code == 200
    assert local_db.get(Configuration, "gowa_monitor_enabled").value == "1"
    assert local_db.get(Configuration, "gowa_monitor_interval_minutes").value == "3"
    assert (
        local_db.get(Configuration, "gowa_monitor_recipients").value
        == "admin@example.com, ops@example.com"
    )
    del settings_form["gowa_monitor_enabled"]
    response = settings_client.post("/config/save", data=settings_form)
    assert response.status_code == 200
    assert local_db.get(Configuration, "gowa_monitor_enabled").value == "0"


@pytest.mark.parametrize(
    ("field", "value", "status_code"),
    [
        ("gowa_monitor_recipients", "invalid", 400),
        ("gowa_monitor_recipients", "", 400),
        ("gowa_enabled", "0", 400),
        ("gowa_monitor_interval_minutes", "0", 422),
        ("gowa_monitor_interval_minutes", "1441", 422),
    ],
)
def test_settings_validation(
    settings_client, settings_form, local_db, *, field, value, status_code
) -> None:
    settings_form[field] = value
    response = settings_client.post("/config/save", data=settings_form)
    assert response.status_code == status_code
    assert local_db.get(Configuration, "gowa_monitor_interval_minutes") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("high_importance", [False, True])
@pytest.mark.parametrize("background", [False, True])
async def test_email_importance_headers_reach_smtp(
    monkeypatch, high_importance, background
) -> None:
    smtp = Mock()
    connection = Mock()
    connection.__enter__ = Mock(return_value=smtp)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(email_helper, "_open_smtp_connection", lambda _config: connection)
    monkeypatch.setattr(
        email_helper,
        "get_smtp_config",
        lambda: {"is_configured": True, "email_from": "bsnap@example.com"},
    )
    tasks = BackgroundTasks() if background else None
    email_helper.send_email(
        ["admin@example.com"],
        "Device alert",
        "Disconnected",
        background_tasks=tasks,
        high_importance=high_importance,
    )
    if tasks is not None:
        smtp.sendmail.assert_not_called()
        await tasks()
    message = Parser().parsestr(smtp.sendmail.call_args.args[2])
    assert message["Importance"] == ("high" if high_importance else None)
    assert message["X-Priority"] == ("1" if high_importance else None)
    assert message["X-MSMail-Priority"] == ("High" if high_importance else None)
