"""Bounded live refresh for cameras exposing a single HTTP snapshot image."""

from __future__ import annotations

import asyncio
import io
import threading

from collections.abc import AsyncIterator, Callable
from time import monotonic
from urllib.parse import unquote, urlsplit, urlunsplit

import requests

from fastapi import HTTPException
from PIL import Image
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from app.utils.live_stream import BOUNDARY, FIRST_FRAME_SECONDS, STREAM_SECONDS

MAX_IMAGE_BYTES = 8 * 1024 * 1024
REFRESH_SECONDS = 2


class SnapshotPreview:
    """Refresh JPEG/PNG sources without saving footage or starting recording jobs."""

    def __init__(
        self,
        source: str,
        username: str,
        password: str,
        release: Callable[[], None],
        *,
        allowed: Callable[[], bool],
    ) -> None:
        parsed = urlsplit(source)
        self.username = unquote(parsed.username) if parsed.username else username
        self.password = unquote(parsed.password or "") if parsed.username else password
        self.source = urlunsplit(parsed._replace(netloc=parsed.netloc.rsplit("@", 1)[-1]))
        self.release = release
        self.allowed = allowed
        self.deadline = monotonic() + STREAM_SECONDS
        self.closed = False
        self.lock = threading.Lock()

    def first_frame(self) -> bytes:
        """Fetch a bounded image using Basic/Digest auth and emit multipart JPEG."""
        try:
            frame_deadline = min(self.deadline, monotonic() + FIRST_FRAME_SECONDS)
            attempts = (
                [None]
                if not self.username
                else [
                    HTTPBasicAuth(self.username, self.password),
                    HTTPDigestAuth(self.username, self.password),
                ]
            )
            with requests.Session() as session:
                session.trust_env = False
                for auth in attempts:
                    with session.get(
                        self.source,
                        auth=auth,
                        timeout=(5, 5),
                        stream=True,
                        allow_redirects=False,
                        headers={"Cache-Control": "no-cache"},
                    ) as response:
                        if response.status_code in {401, 403}:
                            continue
                        if response.status_code != 200:
                            break
                        data = bytearray()
                        # read1 returns available bytes, so a slow trickle cannot
                        # bypass the wall-clock bound while filling a large chunk.
                        while chunk := response.raw.read1(65536, decode_content=True):
                            if self.closed or monotonic() >= frame_deadline:
                                raise ValueError("Preview closed")
                            data.extend(chunk)
                            if len(data) > MAX_IMAGE_BYTES:
                                raise ValueError("Image too large")
                        with Image.open(io.BytesIO(data)) as image:
                            if image.width * image.height > 20_000_000:
                                raise ValueError("Image dimensions too large")
                            image.thumbnail((1280, 720))
                            output = io.BytesIO()
                            image.convert("RGB").save(output, "JPEG")
                        jpeg = output.getvalue()
                        return (
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                            + jpeg
                            + b"\r\n"
                        )
        except Exception:
            self.close()
            raise HTTPException(
                status_code=502, detail="Camera live preview is unavailable"
            ) from None
        self.close()
        raise HTTPException(status_code=502, detail="Camera live preview is unavailable")

    def close(self) -> None:
        """Release the shared viewer slot exactly once."""
        with self.lock:
            if not self.closed:
                self.closed = True
                self.release()

    async def chunks(self, first: bytes) -> AsyncIterator[bytes]:
        """Refresh until disconnect, policy revocation, or the viewer deadline."""
        try:
            yield first
            while not self.closed and monotonic() < self.deadline:
                await asyncio.sleep(REFRESH_SECONDS)
                if self.closed or monotonic() >= self.deadline:
                    break
                if not await asyncio.to_thread(self.allowed):
                    break
                yield await asyncio.to_thread(self.first_frame)
        except Exception:
            # Headers are already sent; terminate on source/policy errors.
            return
        finally:
            self.close()
