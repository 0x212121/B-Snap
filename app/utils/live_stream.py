"""Bounded FFmpeg live previews with deterministic process cleanup."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading

from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from time import monotonic
from urllib.parse import quote, urlparse, urlunparse

from fastapi import HTTPException

from app.models.camera import Camera
from app.utils.map_live_access import LIVE_POLICY_INTERVAL_SECONDS
from app.utils.video import _ffmpeg_executable, _stream_input_args

MAX_STREAMS = 8
MAX_USER_STREAMS = 2
STREAM_SECONDS = 180
FIRST_FRAME_SECONDS = 20
BOUNDARY = "bsnap-live"
_lock = threading.Lock()
_viewers: dict[str, int] = {}


def configured_rtsp_source(camera: Camera) -> str | None:
    """Use an explicit RTSP snapshot source with existing encrypted credentials."""
    source = (camera.snapshot_url or "").strip()
    try:
        parsed = urlparse(source)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
        return None
    if camera.username and "@" not in parsed.netloc:
        auth = f"{quote(camera.username, safe='')}:{quote(camera.password or '', safe='')}"
        parsed = parsed._replace(netloc=f"{auth}@{parsed.netloc}")
    return urlunparse(parsed)


def reserve_viewer(user_id: str) -> Callable[[], None]:
    """Reserve a preview slot per worker, with an idempotent release callback."""
    with _lock:
        if sum(_viewers.values()) >= MAX_STREAMS or _viewers.get(user_id, 0) >= MAX_USER_STREAMS:
            raise HTTPException(status_code=429, detail="Live preview capacity reached")
        _viewers[user_id] = _viewers.get(user_id, 0) + 1
    released = False

    def release() -> None:
        nonlocal released
        with _lock:
            if released:
                return
            released = True
            remaining = _viewers[user_id] - 1
            if remaining:
                _viewers[user_id] = remaining
            else:
                del _viewers[user_id]

    return release


def build_live_command(source: str) -> list[str]:
    """Decode a configured camera source to limited-size multipart JPEG frames."""
    return [
        _ffmpeg_executable(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-timeout" if urlparse(source).scheme.lower() in {"rtsp", "rtsps"} else "-rw_timeout",
        "15000000",
        *_stream_input_args(source),
        "-t",
        str(STREAM_SECONDS),
        "-map",
        "0:v:0",
        "-an",
        "-threads",
        "1",
        "-vf",
        "fps=10,scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v",
        "mjpeg",
        "-q:v",
        "5",
        "-f",
        "mpjpeg",
        "-boundary_tag",
        BOUNDARY,
        "pipe:1",
    ]


class LivePreview:
    """Own a process, its wall-clock watchdog, and its allocated viewer slot."""

    def __init__(
        self, source: str, release: Callable[[], None], *, allowed: Callable[[], bool] | None = None
    ) -> None:
        self.release = release
        self.allowed = allowed
        self.process: subprocess.Popen | None = None
        self.watchdog: threading.Timer | None = None
        self.closed = False
        self.close_lock = threading.Lock()
        try:
            self.process = subprocess.Popen(
                build_live_command(source),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.watchdog = threading.Timer(STREAM_SECONDS, self.close)
            self.watchdog.daemon = True
            self.watchdog.start()
        except OSError:
            self.close()
            raise HTTPException(status_code=503, detail="Live preview requires FFmpeg") from None

    def read_chunk(self) -> bytes:
        """Read available output instead of waiting for a full buffer."""
        if self.closed or self.process is None or self.process.stdout is None:
            return b""
        try:
            return self.process.stdout.read1(65536)
        except (OSError, ValueError):
            return b""

    def first_frame(self) -> bytes:
        """Reject unreachable/invalid sources before sending an HTTP 200 response."""
        timeout = threading.Timer(FIRST_FRAME_SECONDS, self.stop_process)
        timeout.daemon = True
        timeout.start()
        output = bytearray()
        try:
            while len(output) <= 2 * 1024 * 1024:
                chunk = self.read_chunk()
                if not chunk:
                    break
                output.extend(chunk)
                if b"\xff\xd9" in output:
                    return bytes(output)
        finally:
            timeout.cancel()
        self.close()
        raise HTTPException(status_code=502, detail="Camera live preview is unavailable")

    def stop_process(self) -> None:
        """Kill a blocked source read without exposing FFmpeg diagnostics."""
        if self.process is not None and self.process.poll() is None:
            with suppress(OSError):
                self.process.kill()

    def close(self) -> None:
        """Release every resource once, including after disconnect or timeout."""
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
            if self.watchdog is not None:
                self.watchdog.cancel()
            self.stop_process()
            if self.process is not None:
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.stop_process()
                if self.process.stdout is not None:
                    self.process.stdout.close()
            self.release()

    async def chunks(self, first: bytes) -> AsyncIterator[bytes]:
        """Stream frames and kill the process when the browser cancels its request."""
        try:
            next_policy_check = monotonic() + LIVE_POLICY_INTERVAL_SECONDS
            yield first
            while chunk := await asyncio.to_thread(self.read_chunk):
                if self.allowed is not None and monotonic() >= next_policy_check:
                    try:
                        permitted = await asyncio.to_thread(self.allowed)
                    except Exception:
                        permitted = False
                    if not permitted:
                        break
                    next_policy_check = monotonic() + LIVE_POLICY_INTERVAL_SECONDS
                yield chunk
        finally:
            # Synchronous cleanup is bounded and must also run in a cancelled scope.
            self.close()
