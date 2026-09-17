"""Disposable scheduled snapshot worker; never start APScheduler here."""

from __future__ import annotations

import sys


def main(camera_id: str) -> int:
    """Load fresh camera state and execute the existing capture/metadata pipeline."""
    from app.db.database import SessionLocal
    from app.jobs.scheduler import run_snapshot
    from app.models.camera import Camera

    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        if camera is None or camera.status not in ("Active", "Restricted"):
            return 2
        result = run_snapshot(camera)
    return {"success": 0, "skipped": 2}.get(result.get("status"), 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
