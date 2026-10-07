"""Webhook reply routing with mocked persistence and gateway; no messages are sent."""

from __future__ import annotations

import asyncio
import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.routes import wa_webhook


class ReplyRoutingTests(unittest.TestCase):
    def test_processing_and_api_results_quote_the_originating_chat(self) -> None:
        for chat_id in ("record-group@g.us", "628123456789@s.whatsapp.net"):
            for quote_reply in (True, False):
                for response_type in ("text", "image", "video"):
                    with self.subTest(
                        chat_id=chat_id, quote_reply=quote_reply, response_type=response_type
                    ):
                        self.check_routing(
                            chat_id,
                            quote_reply,
                            private_response=False,
                            response_type=response_type,
                        )

    def test_private_api_replies_do_not_quote_the_group_message(self) -> None:
        self.check_routing("record-group@g.us", True, private_response=True)

    def check_routing(
        self, chat_id: str, quote_reply: bool, private_response: bool, response_type: str = "video"
    ) -> None:
        sender = "628123456789"
        message_id = "original-command-id"
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = None
        bot = Mock(
            private_response=private_response,
            quote_reply=quote_reply,
            outbound_items=[
                {
                    "text": "Recorded",
                    **(
                        {"video_path": "recorded.mp4"}
                        if response_type == "video"
                        else {"image_path": "snapshot.jpg"} if response_type == "image" else {}
                    ),
                }
            ],
        )
        bot.parse_command.return_value = ("record", "camera")

        async def handle(*args: object, **kwargs: object) -> str:
            await bot.progress_sender({"text": "Recording"})
            return "Recorded"

        bot.handle = AsyncMock(side_effect=handle)
        request = SimpleNamespace(
            json=AsyncMock(
                return_value={
                    "from": sender,
                    "body": "/record camera",
                    "id": message_id,
                    "chat_id": chat_id,
                }
            )
        )
        gateway = Mock()
        gateway.send_video_file.return_value = {"success": True}
        gateway.send_image_file.return_value = {"success": True}
        gateway.send_text.return_value = {"success": True}
        with (
            patch.object(wa_webhook, "WABotHandler", return_value=bot),
            patch.object(wa_webhook, "_whitelisted_sender", return_value=Mock()),
            patch.object(wa_webhook, "get_command_settings", return_value=[]),
            patch.object(wa_webhook, "match_command", return_value=(None, "")),
            patch.object(wa_webhook, "WAGatewayService", return_value=gateway),
            patch.object(wa_webhook, "_record_wa_message"),
            patch.object(
                wa_webhook, "_send_wa_progress_item", return_value={"success": True}
            ) as progress,
        ):
            result = asyncio.run(wa_webhook.gowa_webhook(request, db))
        assert result.status_code == 200
        recipient = sender if private_response else chat_id
        reply_to = message_id if quote_reply and not private_response else None
        progress.assert_called_once_with(
            recipient, {"text": "Recording"}, sender, "record", reply_to
        )
        if response_type == "video":
            gateway.send_video_file.assert_called_once_with(
                recipient, "recorded.mp4", "Recorded", reply_to=reply_to
            )
        elif response_type == "image":
            gateway.send_image_file.assert_called_once_with(
                recipient, "snapshot.jpg", "Recorded", reply_to=reply_to
            )
        else:
            gateway.send_text.assert_called_once_with(recipient, "Recorded", reply_to=reply_to)
        bot.handle.assert_awaited_once_with(
            sender, "/record camera", is_group=chat_id.endswith("@g.us")
        )


if __name__ == "__main__":
    unittest.main()
