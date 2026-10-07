"""Isolated health and shutdown checks; never run camera jobs or contact PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import runpy
import threading

from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import dotenv
import pytest
import sqlalchemy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import readiness
from app.core import logging_config, service_health
from app.db import database
from app.jobs import scheduler as worker, scheduler_main
from app.middleware import auth_and_setup, observability
from app.ws import notifier


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override incompatible global SQLite schema fixture for mocked lifecycle tests."""


@pytest.fixture(autouse=True)
def isolated_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr(service_health, "HEALTH_DIR", tmp_path)


def test_heartbeat_is_cleared_and_rejects_invalid_or_stale_data():
    assert not service_health.is_healthy("scheduler")
    service_health.write_heartbeat("scheduler")
    assert service_health.is_healthy("scheduler")
    path = service_health.heartbeat_path("scheduler")
    for value in ("invalid", "{}", json.dumps({"pid": -1, "time": 1})):
        path.write_text(value)
        assert not service_health.is_healthy("scheduler")
    service_health.write_heartbeat("scheduler")
    data = json.loads(path.read_text())
    data["time"] -= 181
    path.write_text(json.dumps(data))
    assert not service_health.is_healthy("scheduler")
    service_health.clear_heartbeat("scheduler")
    assert not path.exists()


@pytest.mark.parametrize(
    ("ready", "failed", "status"), [(False, False, 503), (True, False, 200), (True, True, 503)]
)
def test_readiness_bypasses_setup_and_does_not_log_api_rows(monkeypatch, ready, failed, status):
    app = FastAPI()
    app.state.ready = ready
    app.include_router(readiness.router)
    app.add_middleware(auth_and_setup.AuthAndSetupMiddleware)
    app.add_middleware(observability.ObservabilityMiddleware)
    no_auth_db = MagicMock(side_effect=AssertionError("Readiness queried authentication DB"))
    monkeypatch.setattr(auth_and_setup, "SessionLocal", no_auth_db)
    monkeypatch.setattr(observability, "SessionLocal", no_auth_db)
    connection = MagicMock()
    connect = MagicMock(return_value=nullcontext(connection))
    if failed:
        connect.side_effect = RuntimeError("synthetic-sensitive-error")
    monkeypatch.setattr(readiness.engine, "connect", connect)
    with TestClient(app) as client:
        response = client.get(
            "/readyz", headers={"Accept": "application/json"}, follow_redirects=False
        )
    assert response.status_code == status
    assert "synthetic-sensitive-error" not in response.text
    assert connect.call_count == int(ready)
    no_auth_db.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_bypass_does_not_apply_to_other_paths(monkeypatch):
    middleware = auth_and_setup.AuthAndSetupMiddleware(FastAPI())
    db = MagicMock(side_effect=RuntimeError("DB access attempted"))
    monkeypatch.setattr(auth_and_setup, "SessionLocal", db)
    for path, method in (("/readyz-extra", "GET"), ("/readyz", "POST")):
        request = Request({"type": "http", "method": method, "path": path, "headers": []})
        with pytest.raises(RuntimeError, match="DB access attempted"):
            await middleware.dispatch(request, AsyncMock())
    assert db.call_count == 2


@pytest.mark.parametrize("failure", [None, "reload", "database", "thread"])
def test_scheduler_finishes_active_jobs_and_removes_health(monkeypatch, failure):
    stop = threading.Event()
    scheduler = MagicMock(running=True)
    scheduler._thread.is_alive.return_value = failure != "thread"
    start = MagicMock()
    update = MagicMock(return_value=failure != "reload")
    monkeypatch.setattr(worker, "scheduler", scheduler)
    monkeypatch.setattr(worker, "start_scheduler", start)
    monkeypatch.setattr(worker, "update_scheduler_config", update)
    connect = MagicMock(return_value=nullcontext(MagicMock()))
    if failure == "database":
        connect.side_effect = RuntimeError("DB unavailable")
    monkeypatch.setattr(database.engine, "connect", connect)

    def wait_and_stop(timeout):
        assert service_health.is_healthy("scheduler")
        assert timeout == 60
        stop.set()

    monkeypatch.setattr(stop, "wait", wait_and_stop)
    if failure:
        with pytest.raises(RuntimeError):
            scheduler_main.run_scheduler(stop)
    else:
        scheduler_main.run_scheduler(stop)
    start.assert_called_once()
    scheduler.pause.assert_called_once()
    scheduler.shutdown.assert_called_once_with(wait=True)
    assert not service_health.is_healthy("scheduler")


def fake_connection():
    conn = MagicMock()
    conn.is_closed.return_value = False
    for name in ("add_listener", "remove_listener", "fetchval", "execute", "close"):
        setattr(conn, name, AsyncMock())
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return conn


@pytest.mark.asyncio
async def test_notifier_drains_notifications_before_closing(monkeypatch, capsys):
    stop = asyncio.Event()
    conn = fake_connection()
    callback = None

    async def add_listener(channel, handler):
        nonlocal callback
        callback = handler
        assert channel == notifier.PG_NOTIFY_CHANNEL

    async def ping(query):
        assert query == "SELECT 1"
        callback(conn, 123, notifier.PG_NOTIFY_CHANNEL, "private-payload-one")
        callback(conn, 123, notifier.PG_NOTIFY_CHANNEL, "private-payload-two")
        await asyncio.sleep(0)
        stop.set()
        return 1

    conn.add_listener.side_effect = add_listener
    conn.fetchval.side_effect = ping
    monkeypatch.setattr(notifier.asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://test@localhost/test")
    await asyncio.wait_for(notifier.pg_listen_forever(stop), timeout=2)
    assert conn.execute.await_count == 4
    assert conn.transaction.call_count == 2
    assert [call.args[1] for call in conn.execute.await_args_list if len(call.args) == 2] == [
        "private-payload-one",
        "private-payload-two",
    ]
    conn.remove_listener.assert_awaited_once()
    conn.close.assert_awaited_once_with(timeout=5)
    assert not service_health.is_healthy("notifier")
    assert "private-payload" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_notifier_closes_failed_connection_and_reconnects(monkeypatch, capsys):
    stop = asyncio.Event()
    first, second = fake_connection(), fake_connection()
    first.fetchval.side_effect = OSError("synthetic-secret")

    async def ping(query):
        stop.set()
        return 1

    second.fetchval.side_effect = ping
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(notifier.asyncpg, "connect", connect)
    monkeypatch.setattr(notifier, "RETRY_INTERVAL", 0.001)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/test")
    await asyncio.wait_for(notifier.pg_listen_forever(stop), timeout=2)
    assert connect.await_count == 2
    first.close.assert_awaited_once_with(timeout=5)
    second.close.assert_awaited_once_with(timeout=5)
    assert not service_health.is_healthy("notifier")
    assert "synthetic-secret" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("size", "overflow", "fails"),
    [(None, None, False), ("2", "0", False), ("0", "1", True), ("2", "-1", True)],
)
def test_database_pool_configuration_is_bounded(monkeypatch, size, overflow, fails):
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE"):
        monkeypatch.delenv(name, raising=False)
    if size is not None:
        monkeypatch.setenv("DB_POOL_SIZE", size)
    if overflow is not None:
        monkeypatch.setenv("DB_MAX_OVERFLOW", overflow)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://test@localhost/test")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    monkeypatch.setattr(logging_config, "setup_logging", lambda: None)
    create = MagicMock(return_value=database.engine)
    monkeypatch.setattr(sqlalchemy, "create_engine", create)
    path = str(Path(database.__file__))
    if fails:
        with pytest.raises(ValueError, match="must be at least"):
            runpy.run_path(path)
        create.assert_not_called()
    else:
        runpy.run_path(path)
        options = create.call_args.kwargs
        assert options["pool_size"] == (20 if size is None else 2)
        assert options["max_overflow"] == (10 if overflow is None else 0)
        assert options["pool_pre_ping"] is True
        assert options["connect_args"] == {"options": "-c timezone=utc", "connect_timeout": 5}


def test_scheduler_signal_handlers_request_stop(monkeypatch):
    handlers = {}
    monkeypatch.setattr(scheduler_main, "setup_logging", lambda: None)
    monkeypatch.setattr(
        scheduler_main.signal, "signal", lambda signum, handler: handlers.update({signum: handler})
    )

    def run(stop):
        for signum in (scheduler_main.signal.SIGTERM, scheduler_main.signal.SIGINT):
            stop.clear()
            handlers[signum](signum, None)
            assert stop.is_set()

    monkeypatch.setattr(scheduler_main, "run_scheduler", run)
    scheduler_main.main()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_notifier_signal_handlers_request_stop(monkeypatch, fallback):
    loop = asyncio.get_running_loop()
    handlers = {}

    def register(signum, handler):
        if fallback:
            raise NotImplementedError
        handlers[signum] = handler

    monkeypatch.setattr(loop, "add_signal_handler", register)
    monkeypatch.setattr(
        notifier.signal, "signal", lambda signum, handler: handlers.update({signum: handler})
    )

    async def listen(stop):
        for signum in (notifier.signal.SIGTERM, notifier.signal.SIGINT):
            stop.clear()
            if fallback:
                handlers[signum](signum, None)
                await asyncio.sleep(0)
            else:
                handlers[signum]()
            assert stop.is_set()

    monkeypatch.setattr(notifier, "pg_listen_forever", listen)
    await notifier.main()
