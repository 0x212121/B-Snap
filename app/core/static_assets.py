"""Register bundled static assets without requiring a local documentation build."""

from __future__ import annotations

import logging

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger("main")


class PublicStaticFiles(StaticFiles):
    """Exclude private footage after the file server normalizes the request path."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Check both the normalized URL and resolved file before serving it."""
        # Router-level GET blockers cannot cover repeated separators, HEAD, or
        # filesystem aliases. Also check the resolved target for in-tree symlinks.
        parts = path.replace("\\", "/").strip("/").split("/")
        protected = {"snapshots", "videos"}
        if parts[0].casefold() in protected:
            raise HTTPException(status_code=403, detail="Direct footage access is forbidden")
        if self.directory is not None:
            root = Path(self.directory).resolve()
            target = (root / path).resolve()
            if target.is_relative_to(root):
                relative = target.relative_to(root)
                if relative.parts and relative.parts[0].casefold() in protected:
                    raise HTTPException(
                        status_code=403, detail="Direct footage access is forbidden"
                    )
        return await super().get_response(path, scope)


def mount_documentation(app: FastAPI, root: Path) -> None:
    """Serve generated documentation when bundled; otherwise leave its URL as 404."""
    directory = root / "docs" / "build" / "html"
    if directory.is_dir():
        app.mount("/documentation", StaticFiles(directory=directory), name="docs")
    else:
        logger.info("Built documentation is absent; /documentation is unavailable.")
