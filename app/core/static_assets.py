"""Register bundled static assets without requiring a local documentation build."""

from __future__ import annotations

import logging

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("main")


def mount_documentation(app: FastAPI, root: Path) -> None:
    """Serve generated documentation when bundled; otherwise leave its URL as 404."""
    directory = root / "docs" / "build" / "html"
    if directory.is_dir():
        app.mount("/documentation", StaticFiles(directory=directory), name="docs")
    else:
        logger.info("Built documentation is absent; /documentation is unavailable.")
