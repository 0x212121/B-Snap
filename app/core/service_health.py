"""Process-local worker heartbeats, independent of application database imports."""

from __future__ import annotations

import argparse
import json
import os
import time

from pathlib import Path

HEALTH_DIR = Path(os.getenv("BSNAP_HEALTH_DIR", "/tmp/bsnap-health"))
MAX_AGE = {"scheduler": 180, "notifier": 90}


def clear_heartbeat(service: str) -> None:
    """Invalidate health on startup, disconnection and shutdown."""
    heartbeat_path(service).unlink(missing_ok=True)


def heartbeat_path(service: str) -> Path:
    """Return the heartbeat location for a known worker role."""
    if service not in MAX_AGE:
        raise ValueError("Unknown worker service")
    return HEALTH_DIR / f"{service}.json"


def write_heartbeat(service: str) -> None:
    """Publish an atomic heartbeat after a successful worker check."""
    path = heartbeat_path(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "time": time.monotonic()}), encoding="utf-8"
    )
    temporary.replace(path)


def is_healthy(service: str) -> bool:
    """Reject missing/stale heartbeats and workers whose process has exited."""
    try:
        data = json.loads(heartbeat_path(service).read_text(encoding="utf-8"))
        age = time.monotonic() - data["time"]
        if not 0 <= age <= MAX_AGE[service] or data["pid"] <= 0:
            return False
        # Signal zero checks existence on POSIX; Windows kill has different semantics.
        if os.name != "nt":
            os.kill(data["pid"], 0)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def main() -> int:
    """Return a healthcheck exit code for the requested worker."""
    parser = argparse.ArgumentParser(description="Check a B-Snap worker heartbeat")
    parser.add_argument("service", choices=MAX_AGE)
    args = parser.parse_args()
    return 0 if is_healthy(args.service) else 1


if __name__ == "__main__":
    raise SystemExit(main())
