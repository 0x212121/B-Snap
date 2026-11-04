"""HTTP live refresh uses bounded images, safe errors, and deterministic cleanup."""

from __future__ import annotations

import asyncio
import io

from unittest.mock import Mock

import pytest

from fastapi import HTTPException
from PIL import Image

from app.utils import live_snapshot


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(output, "PNG")
    return output.getvalue()


def mock_session(monkeypatch, responses):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock()
    session.get.side_effect = responses
    monkeypatch.setattr(live_snapshot.requests, "Session", lambda: session)
    return session


def response(status, data=b""):
    result = Mock(status_code=status)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock()
    result.raw.read1.side_effect = [data, b""]
    return result


def test_snapshot_basic_digest_and_jpeg_conversion(monkeypatch):
    session = mock_session(monkeypatch, [response(401), response(200, image_bytes())])
    release = Mock()
    preview = live_snapshot.SnapshotPreview(
        "https://camera.example/snapshot", "viewer", "secret", release, allowed=lambda: True
    )
    first = preview.first_frame()
    assert first.startswith(b"--bsnap-live\r\nContent-Type: image/jpeg")
    assert b"\xff\xd8" in first
    assert session.get.call_count == 2
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.trust_env is False
    preview.close()
    preview.close()
    release.assert_called_once()


@pytest.mark.parametrize(
    ("status", "data"),
    [(302, b""), (500, b""), (200, b"invalid"), (200, b"x" * (live_snapshot.MAX_IMAGE_BYTES + 1))],
    ids=["redirect", "server-error", "invalid-image", "oversized"],
)
def test_bad_image_closes_slot_and_hides_source(monkeypatch, status, data):
    mock_session(monkeypatch, [response(status, data)])
    release = Mock()
    preview = live_snapshot.SnapshotPreview(
        "https://viewer:secret@camera.example/image", "", "", release, allowed=lambda: True
    )
    assert "secret" not in preview.source
    with pytest.raises(HTTPException) as error:
        preview.first_frame()
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail
    release.assert_called_once()


def test_revoked_policy_stops_before_next_camera_request(monkeypatch):
    mock_session(monkeypatch, [response(200, image_bytes())])
    release = Mock()
    preview = live_snapshot.SnapshotPreview(
        "https://camera.example/image", "", "", release, allowed=lambda: False
    )
    first = preview.first_frame()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(live_snapshot.asyncio, "sleep", no_sleep)

    async def collect():
        return [chunk async for chunk in preview.chunks(first)]

    assert asyncio.run(collect()) == [first]
    release.assert_called_once()
