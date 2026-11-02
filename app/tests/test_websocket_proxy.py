"""Authenticated WebSockets and toast delivery through HTTPS-terminating proxies."""

from __future__ import annotations

import json

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.ws import record_notifications, routes
from app.ws.manager import websocket_connections

PUBLIC_ORIGIN = "https://camera.example"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema setup; sessions are real and DB/transport are isolated."""


@pytest.fixture
def proxy_client(monkeypatch):
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    user = SimpleNamespace(
        id=7,
        group_id=None,
        web_tokens=[
            {"token": "browser", "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        ],
    )
    factory = MagicMock()
    db = factory.return_value.__enter__.return_value
    db.query.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr(record_notifications, "SessionLocal", factory)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="isolated-proxy-session")
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1"])
    app.include_router(routes.router)

    @app.get("/session")
    def session(request: Request):
        request.session["user_id"] = 7
        response = JSONResponse({"ok": True})
        response.set_cookie("session_token", "browser", httponly=True)
        return response

    with TestClient(app, base_url="http://camera.example", client=("127.0.0.1", 12345)) as client:
        client.get("/session")
        yield client, user


@pytest.mark.parametrize(
    ("public_origin", "host", "forwarded_proto", "origin"),
    [
        (PUBLIC_ORIGIN, "b-snap:8080", "https", PUBLIC_ORIGIN),
        (PUBLIC_ORIGIN, "b-snap:8080", "http", PUBLIC_ORIGIN),
        (PUBLIC_ORIGIN, "b-snap:8080", None, PUBLIC_ORIGIN),
        (PUBLIC_ORIGIN, "camera.example:443", "https", PUBLIC_ORIGIN),
        (None, "camera.example", "https", PUBLIC_ORIGIN),
        (None, "camera.example:443", "https", PUBLIC_ORIGIN),
        (None, "camera.example", None, "http://camera.example"),
    ],
)
def test_proxy_handshake_delivers_toast_with_live_session(
    proxy_client, monkeypatch, *, public_origin, host, forwarded_proto, origin
):
    client, _user = proxy_client
    if public_origin:
        monkeypatch.setenv("PUBLIC_ORIGIN", public_origin)
    headers = {"Host": host, "Origin": origin}
    if forwarded_proto:
        headers["X-Forwarded-Proto"] = forwarded_proto
    with client.websocket_connect("ws://camera.example/ws", headers=headers) as socket:
        payload = {
            "type": "toast",
            "data": {"user_id": 7, "message": "Proxy delivery", "type": "info"},
        }
        for server_socket in tuple(websocket_connections):
            client.portal.call(
                record_notifications.deliver_notification,
                server_socket,
                payload,
                json.dumps(payload),
            )
        assert socket.receive_json() == payload
    assert not websocket_connections


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        "http://camera.example",
        "https://camera.example:444",
        "https://camera.example.evil.example",
        "http://[broken",
    ],
)
def test_public_origin_never_allows_unrelated_browser_origins(proxy_client, monkeypatch, origin):
    client, _user = proxy_client
    monkeypatch.setenv("PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    with (
        pytest.raises(WebSocketDisconnect) as error,
        client.websocket_connect(
            "ws://camera.example/ws",
            headers={"Host": "b-snap:8080", "Origin": origin, "X-Forwarded-Host": "evil.example"},
        ),
    ):
        pass
    assert error.value.code == 1008


def test_proxy_origin_does_not_bypass_missing_or_revoked_session(proxy_client, monkeypatch):
    client, user = proxy_client
    monkeypatch.setenv("PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    headers = {"Host": "b-snap:8080", "Origin": PUBLIC_ORIGIN}
    user.web_tokens.clear()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("ws://camera.example/ws", headers=headers),
    ):
        pass
    client.cookies.clear()
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("ws://camera.example/ws", headers=headers),
    ):
        pass


def test_proxy_toast_rechecks_credentials_for_every_delivery(proxy_client, monkeypatch):
    client, user = proxy_client
    monkeypatch.setenv("PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    with client.websocket_connect(
        "ws://camera.example/ws", headers={"Host": "b-snap:8080", "Origin": PUBLIC_ORIGIN}
    ) as socket:
        server_socket = next(iter(websocket_connections))
        send = Mock()

        async def capture(message):
            send(message)

        monkeypatch.setattr(server_socket, "send_text", capture)
        payload = {"type": "toast", "data": {"user_id": 8, "message": "Other user"}}
        client.portal.call(
            record_notifications.deliver_notification, server_socket, payload, json.dumps(payload)
        )
        user.web_tokens.clear()
        payload["data"]["user_id"] = 7
        client.portal.call(
            record_notifications.deliver_notification, server_socket, payload, json.dumps(payload)
        )
        send.assert_not_called()
        socket.send_text("heartbeat")


def test_duplicate_origins_are_rejected_with_public_origin(monkeypatch):
    monkeypatch.setenv("PUBLIC_ORIGIN", PUBLIC_ORIGIN)
    websocket = WebSocket(
        {
            "type": "websocket",
            "scheme": "ws",
            "path": "/ws",
            "headers": [(b"origin", PUBLIC_ORIGIN.encode()), (b"origin", b"https://evil.example")],
            "server": ("b-snap", 8080),
        },
        receive=AsyncMock(),
        send=AsyncMock(),
    )
    assert not routes._websocket_origin_allowed(websocket)


def test_forwarded_host_is_not_an_origin_allowlist(proxy_client):
    client, _user = proxy_client
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "ws://camera.example/ws",
            headers={
                "Host": "b-snap:8080",
                "Origin": PUBLIC_ORIGIN,
                "X-Forwarded-Host": "camera.example",
                "X-Forwarded-Proto": "https",
            },
        ),
    ):
        pass
