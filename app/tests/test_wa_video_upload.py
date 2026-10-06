"""Local multipart upload tests; no WhatsApp messages are sent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.utils.wa_gateway import WAGatewayService


class VideoUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WAGatewayService.__new__(WAGatewayService)
        self.service.config = SimpleNamespace(
            is_configured=lambda: True, base_url="http://gowa-test", device_id=None
        )
        self.service.session = Mock()
        self.service.send_timeout_seconds = 10
        self.service.send_retries = 1

    def test_video_upload_rewinds_file_on_retry_and_preserves_caption(self) -> None:
        uploads = []

        def post(url: str, **kwargs) -> Mock:
            name, stream, content_type = kwargs["files"]["video"]
            uploads.append((url, stream.read(), kwargs["data"], content_type))
            response = Mock(status_code=500 if len(uploads) == 1 else 200)
            response.json.return_value = {"results": {"message_id": "sent-id"}}
            return response

        self.service.session.post.side_effect = post
        with tempfile.TemporaryDirectory() as folder, patch("app.utils.wa_gateway.time.sleep"):
            path = Path(folder) / "test.mp4"
            path.write_bytes(b"recorded video")
            result = self.service.send_video_file("628123456789", str(path), "Camera Gate", "reply-id")
        self.assertTrue(result["success"])
        self.assertEqual(result["message_id"], "sent-id")
        self.assertEqual(len(uploads), 2)
        for url, content, data, content_type in uploads:
            self.assertEqual(url, "http://gowa-test/send/video")
            self.assertEqual(content, b"recorded video")
            self.assertEqual(content_type, "video/mp4")
            self.assertEqual(data, {
                "phone": "628123456789@s.whatsapp.net",
                "caption": "Camera Gate", "reply_message_id": "reply-id",
            })

    def test_missing_video_never_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            result = self.service.send_video_file("628123456789", str(Path(folder) / "missing.mp4"))
        self.assertFalse(result["success"])
        self.service.session.post.assert_not_called()

    def test_multipart_text_fields_remain_supported(self) -> None:
        response = Mock(status_code=200)
        self.service.session.post.return_value = response
        self.assertIs(self.service._post_with_retry(
            "http://gowa-test/send/image", files=[("phone", (None, "628123456789"))]
        ), response)


if __name__ == "__main__":
    unittest.main()
