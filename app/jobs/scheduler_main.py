"""Dedicated scheduler process with responsive shutdown and health reporting."""

from __future__ import annotations

# Database and scheduler imports are deferred to keep the CLI import side-effect free.
# ruff: noqa: PLC0415
import logging
import signal
import threading

from pathlib import Path

from app.core.logging_config import setup_logging
from app.core.service_health import clear_heartbeat, write_heartbeat


def run_scheduler(stop: threading.Event) -> None:
    """Poll configuration and stop scheduling before waiting for active jobs."""
    # Importing the entrypoint must not start jobs or open database connections.
    from sqlalchemy import text

    from app.db.database import engine
    from app.jobs.scheduler import scheduler, start_scheduler, update_scheduler_config

    logger = logging.getLogger("scheduler")
    clear_heartbeat("scheduler")
    try:
        start_scheduler()
        logger.info("Scheduler started in the background.")
        while not stop.is_set():
            if not update_scheduler_config():
                raise RuntimeError("Scheduler configuration reload failed")
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            if not scheduler.running or not scheduler._thread or not scheduler._thread.is_alive():
                raise RuntimeError("Scheduler is no longer running")
            flag = Path("/tmp/shared/reload_scheduler.flag")
            if flag.exists():
                flag.unlink(missing_ok=True)
                logger.info("Scheduler config reloaded from trigger.")
            write_heartbeat("scheduler")
            stop.wait(60)
    finally:
        clear_heartbeat("scheduler")
        if scheduler.running:
            scheduler.pause()
            logger.info("Waiting for active scheduler jobs to finish.")
            scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")


def main() -> None:
    """Register termination handlers and run the scheduler worker."""
    setup_logging()
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    run_scheduler(stop)


if __name__ == "__main__":
    main()
