"""Bounded in-memory previews; source evidence files are never modified."""

from __future__ import annotations

import asyncio
import logging

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from fastapi.responses import FileResponse, Response
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


async def snapshot_preview_response(path: str) -> Response:
    """Render off the event loop, falling back to the original for unreadable images."""
    headers = {"Cache-Control": "private, no-cache"}
    try:
        content = await asyncio.to_thread(snapshot_preview, path)
    except (OSError, ValueError):
        logger.warning("Snapshot preview unavailable; serving original")
        return FileResponse(path, headers=headers)
    return Response(content=content, media_type="image/jpeg", headers=headers)


def snapshot_preview(path: str) -> bytes:
    """Return a small JPEG, invalidating the cache when the source changes."""
    source = Path(path)
    stat = source.stat()
    return _render_preview(str(source.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=128)
def _render_preview(path: str, _modified_ns: int, _size: int) -> bytes:
    # Metadata participates in the cache key, keeping overwritten files fresh.
    with Image.open(path) as source:
        source.draft("RGB", (640, 400))
        image = ImageOps.exif_transpose(source)
        try:
            image.thumbnail((640, 400))
            with image.convert("RGB") as rgb, BytesIO() as output:
                rgb.save(output, format="JPEG", quality=80)
                return output.getvalue()
        finally:
            image.close()
