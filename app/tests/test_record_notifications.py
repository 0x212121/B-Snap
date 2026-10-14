"""Private recording notification tests using mocked, isolated database access."""

from __future__ import annotations

import asyncio
import unittest

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from starlette.websockets import WebSocket

from app.ws import record_notifications


class RecordNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {"type": "record_complete", "source": "browser", "recipient_user_id": 7}
        self.ws = WebSocket(
            {
                "type": "websocket",
                "session": {"user_id": 7},
                "headers": [(b"cookie", b"session_token=browser-token")],
            },
            receive=Mock(),
            send=Mock(),
        )
        self.db_factory = self.enterContext(patch.object(record_notifications, "SessionLocal"))
        self.db = self.db_factory.return_value.__enter__.return_value
        self.user = SimpleNamespace(
            web_tokens=[
                {
                    "token": "browser-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                }
            ]
        )
        self.db.query.return_value.filter.return_value.first.return_value = self.user

    def test_only_recipient_with_valid_session_receives_event(self) -> None:
        assert record_notifications.can_receive_recording(self.ws, self.payload)
        self.ws.scope["session"]["user_id"] = 8
        assert not record_notifications.can_receive_recording(self.ws, self.payload)

    def test_legacy_bot_and_untargeted_events_are_suppressed(self) -> None:
        for payload in (
            {"type": "record_complete"},
            {**self.payload, "source": "bot"},
            {**self.payload, "recipient_user_id": None},
        ):
            assert not record_notifications.can_receive_recording(self.ws, payload)
        self.db_factory.assert_not_called()

    def test_revoked_expired_missing_or_anonymous_sessions_are_suppressed(self) -> None:
        self.user.web_tokens = []
        assert not record_notifications.can_receive_recording(self.ws, self.payload)
        self.user.web_tokens = [
            {
                "token": "browser-token",
                "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            }
        ]
        assert not record_notifications.can_receive_recording(self.ws, self.payload)
        self.db.query.return_value.filter.return_value.first.return_value = None
        assert not record_notifications.can_receive_recording(self.ws, self.payload)
        self.ws.scope["session"] = {}
        assert not record_notifications.can_receive_recording(self.ws, self.payload)

    def test_database_failure_does_not_leak_private_event(self) -> None:
        self.db.query.side_effect = RuntimeError("unavailable")
        assert not record_notifications.can_receive_recording(self.ws, self.payload)

    def test_delivery_does_not_send_recording_contents_to_other_users(self) -> None:
        self.ws.send_text = AsyncMock()
        asyncio.run(record_notifications.deliver_notification(self.ws, self.payload, "private"))
        self.ws.send_text.assert_awaited_once_with("private")
        self.ws.send_text.reset_mock()
        self.ws.scope["session"]["user_id"] = 8
        asyncio.run(record_notifications.deliver_notification(self.ws, self.payload, "private"))
        self.ws.send_text.assert_not_awaited()

    def test_general_notifications_keep_existing_delivery(self) -> None:
        self.ws.send_text = AsyncMock()
        asyncio.run(record_notifications.deliver_notification(self.ws, {"type": "toast"}, "public"))
        self.ws.send_text.assert_awaited_once_with("public")
        self.db_factory.assert_not_called()
