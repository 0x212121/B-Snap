"""OS-owned camera locks released automatically when a worker exits or is killed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

LOCK_DIR = Path(os.environ.get("SNAPSHOT_LOCK_DIR", "/tmp/shared/locks"))

DEFAULT_TTL = 300  # 5 menit


class TTLFileLock:
    """Legacy interface backed by an OS lock; file age never evicts its owner."""

    def __init__(self, lock_path: Path, ttl_seconds: int = DEFAULT_TTL) -> None:
        self.lock_path = Path(lock_path)
        self.ttl_seconds = ttl_seconds
        self._file: BinaryIO | None = None

    def __enter__(self) -> TTLFileLock:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(f"Lock unavailable: {self.lock_path.name}") from exc
        self._file = handle
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        # Never unlink: another process may already hold the same inode.


def get_camera_lock(camera_id: str, ttl_seconds: int = DEFAULT_TTL) -> TTLFileLock:
    """Return a nonblocking lock; legacy TTL argument never evicts a live owner."""
    lock_file = LOCK_DIR / f"{UUID(camera_id)}.lock"
    return TTLFileLock(lock_file, ttl_seconds)
