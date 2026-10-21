"""Isolated map boot and request recovery tests; no application DB schema."""

from __future__ import annotations

import re
import shutil
import subprocess

from html.parser import HTMLParser
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared schema fixture because these tests do not use a DB."""


def test_map_loading_recovery() -> None:
    """Run the real browser scripts with deterministic failures and fake timers."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    subprocess.run(
        [node, str(Path(__file__).with_name("map_loading_harness.cjs"))],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_map_library_assets_are_local_and_complete() -> None:
    """Catch missing Docker/repository assets, including images referenced by CSS."""
    root = Path(__file__).resolve().parents[2]
    urls: list[str] = []

    class Resources(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attributes = dict(attrs)
            if tag == "script" and attributes.get("src"):
                urls.append(attributes["src"])
            if tag == "link" and attributes.get("rel") == "stylesheet":
                urls.append(attributes["href"])

    Resources().feed((root / "templates/maps.html").read_text(encoding="utf-8"))
    assert urls
    assert all(url.startswith("/static/") for url in urls)
    urls.extend(
        re.findall(
            r"'(/static/vendor/[^']+)'",
            (root / "static/js/map-libraries.js").read_text(encoding="utf-8"),
        )
    )
    for url in urls:
        asset = root / url.lstrip("/")
        assert asset.is_file(), url
        if asset.suffix == ".css":
            for image in re.findall(r"url\(['\"]?([^)'\"]+)", asset.read_text(encoding="utf-8")):
                if image.startswith(("#", "data:")):
                    continue
                assert (asset.parent / image).is_file(), image
