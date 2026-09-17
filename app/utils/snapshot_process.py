"""Supervise disposable snapshot processes without inheriting DB connections."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from time import monotonic

logger = logging.getLogger("scheduler")

_attempts: dict[str, float] = {}
_timeouts: dict[str, tuple[int, float]] = {}
_attempts_lock = threading.Lock()


def run_process(command: list[str], timeout: float) -> dict[str, str]:
    """Kill and reap a stalled worker, including stalls in native libraries."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[2],
        **options,
    ) as process:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return {"status": "timeout", "details": "Snapshot worker exceeded deadline"}
        except BaseException:
            process.kill()
            process.wait()
            raise
    return {
        "status": {0: "success", 2: "skipped"}.get(code, "error"),
        "details": f"Snapshot worker exit code: {code}",
    }


def run_camera_process(camera_id: str, timeout: float) -> dict[str, str]:
    """Resolve credentials inside the child; command arguments contain only its ID."""
    with _attempts_lock:
        now = monotonic()
        # Preserve the scheduler's per-camera 30s guard across disposable children.
        for expired in [key for key, started in _attempts.items() if now - started >= 30]:
            del _attempts[expired]
        failures, retry_at = _timeouts.get(camera_id, (0, 0.0))
        if now < retry_at:
            return {"status": "skipped", "details": "timeout_cooldown"}
        if camera_id in _attempts:
            return {"status": "skipped", "details": "rate_limited"}
        _attempts[camera_id] = now
    logger.info("[CAMERA START] %s; hard deadline=%ss", camera_id, timeout)
    result = run_process([sys.executable, "-m", "app.jobs.snapshot_worker", camera_id], timeout)
    with _attempts_lock:
        if result["status"] == "timeout":
            failures += 1
            _timeouts[camera_id] = (failures, monotonic() + 900 if failures >= 3 else 0.0)
        else:
            _timeouts.pop(camera_id, None)
    return result
