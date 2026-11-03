"""Supervise FFmpeg through a private pipe; EOF also stops an orphaned recorder.

The first stdin line is a JSON argument list. Subsequent heartbeat lines keep the
session alive. Credentials never appear in this supervisor's command line or logs.
This module deliberately imports no database or application services.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading

from time import monotonic
from typing import TextIO

FFMPEG_ERRORS = {
    "unknown": "FFmpeg could not record the camera stream. Check stream and codec compatibility.",
    "input_option": "FFmpeg does not support a required camera input option.",
    "authentication": "The camera rejected the recording credentials.",
    "connection": "FFmpeg could not connect to the camera stream.",
    "codec": "The camera video codec cannot be copied into an MP4 archive.",
    "timestamps": "The camera stream has invalid timestamps for MP4 recording.",
    "storage": "Recording storage is full or cannot be written.",
    "executable": "The recorder could not start FFmpeg. Check the scheduler FFmpeg installation.",
}


def classify_ffmpeg_error(line: str) -> str:
    """Return an allowlisted code; never forward raw credential-bearing FFmpeg output."""
    lowered = line.lower()
    if "option" in lowered and ("not found" in lowered or "unrecognized option" in lowered):
        return "input_option"
    patterns = {
        "authentication": ("401 unauthorized", "403 forbidden", "authentication failed"),
        "codec": ("could not find tag for codec", "codec not currently supported in container"),
        "timestamps": ("unknown timestamp", "invalid dts", "non monotonically increasing dts"),
        "storage": ("no space left on device", "permission denied", "read-only file system"),
        "connection": (
            "connection refused",
            "connection timed out",
            "server returned 404",
            "network is unreachable",
        ),
    }
    for code, matches in patterns.items():
        if any(pattern in lowered for pattern in matches):
            return code
    return "unknown"


def read_errors(stream: TextIO, codes: queue.Queue[str]) -> None:
    """Drain stderr continuously while retaining only classified, safe diagnostics."""
    seen: set[str] = set()
    for line in stream:
        code = classify_ffmpeg_error(line)
        if code != "unknown" and code not in seen:
            seen.add(code)
            codes.put(code)


def report_error(code: str) -> None:
    """Send a bounded JSON status to the worker on a separate pipe."""
    print(json.dumps({"recording_error": code}), file=sys.stderr, flush=True)


def read_control(stream: TextIO, messages: queue.Queue[str]) -> None:
    """Wake the supervisor on explicit stop or parent pipe closure."""
    for line in stream:
        messages.put(line.strip())
    messages.put("stop")


def stop_process(process: subprocess.Popen) -> None:
    """Ask FFmpeg to finalize the current MP4 before escalating a stuck process."""
    if process.poll() is not None:
        return
    try:
        process.stdin.write("q\n")
        process.stdin.flush()
        process.wait(timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    """Run only while the owning worker remains responsive."""
    arguments = json.loads(sys.stdin.readline())
    messages: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=read_control, args=(sys.stdin, messages), daemon=True)
    reader.start()
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=sys.stdout,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        report_error("executable")
        return 1
    codes: queue.Queue[str] = queue.Queue()
    errors = threading.Thread(target=read_errors, args=(process.stderr, codes), daemon=True)
    errors.start()
    last_control = monotonic()
    try:
        while process.poll() is None:
            try:
                message = messages.get(timeout=1)
            except queue.Empty:
                if monotonic() - last_control >= 20:
                    break
                continue
            if message == "stop":
                break
            last_control = monotonic()
    finally:
        stop_process(process)
        errors.join(timeout=2)
        process.stderr.close()
        process.stdin.close()
    if process.returncode:
        code = "unknown"
        while not codes.empty():
            code = codes.get_nowait()
        report_error(code)
    return process.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
