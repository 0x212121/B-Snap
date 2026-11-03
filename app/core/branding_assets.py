"""Independent image assets for application identity and the map heading."""

from __future__ import annotations

import os
import re

from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

MAX_LOGO_SIZE = 512 * 1024
MAP_LOGOS = {"/static/branding/map-logo.png", "/static/branding/map-logo.ico"}


def map_logo_url(configs: Mapping[str, str]) -> str | None:
    """Use only the selected map image or a legacy map asset, never app branding."""
    selected = configs.get("map_logo", "")
    candidates = ([selected] if selected in MAP_LOGOS else []) + [
        "/static/icons/logo.png",
        "/static/icons/logo.ico",
    ]
    return next((url for url in candidates if Path(url.lstrip("/")).is_file()), None)


def validate_logo_image(filename: str, content_type: str | None, contents: bytes) -> str:
    """Return a supported suffix after checking size, MIME and actual image format."""
    extension = Path(filename).suffix.lower()
    if extension not in {".png", ".ico"} or content_type not in {
        "image/png",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }:
        raise ValueError("Invalid logo file format. Only .png or .ico are allowed.")
    if len(contents) > MAX_LOGO_SIZE:
        raise ValueError("Uploaded logo file is too large (max 512 KB).")
    try:
        with Image.open(BytesIO(contents)) as image:
            if image.format != {".png": "PNG", ".ico": "ICO"}[extension]:
                raise ValueError("Image format mismatch")
            image.verify()
    except Exception as exc:
        raise ValueError("The uploaded image file is corrupt.") from exc
    return extension


def save_logo_image(stem: str, extension: str, contents: bytes) -> str:
    """Atomically replace one fixed asset without touching other logo identities."""
    if (
        stem not in {"logo", "map-logo"} and not re.fullmatch(r"application-[a-f0-9]{64}", stem)
    ) or extension not in {".png", ".ico"}:
        raise ValueError("Unsupported logo destination")
    directory = Path("static/branding")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stem}{extension}"
    temporary = None
    try:
        with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(contents)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "/" + target.as_posix()
