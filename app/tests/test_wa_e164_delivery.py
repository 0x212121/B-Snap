"""Check E.164 conversion at the GoWA transport boundary without sending messages."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.utils.wa_gateway import WAGatewayService, _gateway_recipient


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the full-schema fixture; these tests mock the gateway."""


@pytest.mark.parametrize("method", ["text", "image", "image_file", "video", "file", "document"])
def test_e164_personal_recipient_at_gateway_boundary(tmp_path, method):
    service = WAGatewayService.__new__(WAGatewayService)
    service.config = SimpleNamespace(is_configured=lambda: True, base_url="http://gowa-test")
    response = Mock(status_code=200)
    response.json.return_value = {"results": {}}
    service._post_with_retry = Mock(return_value=response)
    service.session = SimpleNamespace(post=Mock(return_value=response))
    file = tmp_path / "test.dat"
    file.write_bytes(b"test")
    if method == "text":
        result = service.send_text("+12025550123", "test")
        recipient = service._post_with_retry.call_args.kwargs["json"]["phone"]
    elif method == "image":
        result = service.send_image("+12025550123", "https://example.test/image.png")
        recipient = service._post_with_retry.call_args.kwargs["files"]["phone"][1]
    elif method == "image_file":
        result = service.send_image_file("+12025550123", str(file))
        recipient = service._post_with_retry.call_args.kwargs["data"]["phone"]
    elif method == "video":
        result = service.send_video_file("+12025550123", str(file))
        recipient = service._post_with_retry.call_args.kwargs["data"]["phone"]
    elif method == "file":
        result = service.send_file("+12025550123", str(file))
        recipient = service._post_with_retry.call_args.kwargs["data"]["phone"]
    else:
        result = service.send_document("+12025550123", "https://example.test/file.pdf")
        recipient = service.session.post.call_args.kwargs["json"]["phone"]
    assert result["success"]
    expected = "12025550123" if method in {"file", "document"} else "12025550123@s.whatsapp.net"
    assert recipient == expected


def test_group_and_qualified_personal_recipients_are_preserved():
    assert _gateway_recipient("120363000000@g.us") == "120363000000@g.us"
    assert _gateway_recipient("+12025550123@s.whatsapp.net") == "12025550123@s.whatsapp.net"
