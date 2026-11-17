"""Exercise Trash rendering with untrusted fields and retention holds."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from pathlib import Path

import pytest

from app.utils.template_helper import templates


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared SQLite schema fixture; this test uses no database."""


def test_trash_rows_escape_data_and_preserve_sort_and_hold() -> None:
    """Run the actual renderer without interpolating API strings into HTML or JS."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required")
    root = Path(__file__).resolve().parents[2]
    source = (root / "templates/admin_trash.html").read_text(encoding="utf-8")
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", source, re.S))
    script = templates.env.from_string('{% import "_icons.html" as ui_icons %}' + scripts).render()
    result = subprocess.run(
        [node, str(Path(__file__).with_name("trash_frontend_harness.cjs"))],
        input=json.dumps({"script": script}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
