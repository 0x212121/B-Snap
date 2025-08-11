import os
import time
import threading
import logging
from pathlib import Path
from app.core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("scheduler")

LOCK_DIR = Path("/tmp/shared/locks")
LOCK_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_TTL = 300  # 5 menit


class TTLFileLock:
    def __init__(self, lock_path: Path, ttl_seconds: int = DEFAULT_TTL):
        self.lock_path = Path(lock_path)
        self.ttl_seconds = ttl_seconds
        self._thread_lock = threading.Lock()

    def __enter__(self):
        with self._thread_lock:
            if self.lock_path.exists():
                age = time.time() - self.lock_path.stat().st_mtime
                if age < self.ttl_seconds:
                    raise RuntimeError(f"Lock active: {self.lock_path.name}, age={age:.1f}s")
                else:
                    logger.warning(
                        "[LOCK EXPIRED] Lock %s expired after %.1fs. Removing stale lock.", self.lock_path.name, age
                    )
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        pass
            # Buat lock baru
            self.lock_path.write_text(str(os.getpid()))
            return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except FileNotFoundError:
            pass


def get_camera_lock(camera_id: str, ttl_seconds: int = DEFAULT_TTL):
    """Return TTL-based file lock for a camera."""
    lock_file = LOCK_DIR / f"{camera_id}.lock"
    return TTLFileLock(lock_file, ttl_seconds)
