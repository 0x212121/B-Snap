"""Execute actual log-viewer/date scripts against a deterministic DOM and fetch."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from pathlib import Path

import pytest

from app.routes.logs import LogEntry
from app.utils.template_helper import templates


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """No database/schema/startup is needed for parsing or JavaScript checks."""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-07T16:30:00+00:00", "2026-10-07T16:30:00+00:00"),
        ("2026-10-08T00:30:00+08:00", "2026-10-07T16:30:00+00:00"),
        ("2026-10-07 16:30:00,123", "2026-10-07T16:30:00.123000+00:00"),
    ],
)
def test_log_parser_recognizes_current_and_legacy_utc_formats(raw, expected):
    entry = LogEntry(f"[{raw}] [WARNING] [camera] [pid=42] Camera unavailable", 1)
    assert entry.timestamp == expected
    assert entry.message == "Camera unavailable"
    assert entry.level == "WARNING"
    assert entry.logger == "camera"


def test_log_viewer_escapes_metadata_errors_and_preserves_timezone_on_language_change():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the JavaScript regression check")
    root = Path(__file__).resolve().parents[2]
    source = (root / "templates/logs.html").read_text(encoding="utf-8")
    script = templates.env.from_string(
        "\n".join(re.findall(r"<script>(.*?)</script>", source, re.S))
    ).render(server_timezone="Asia/Makassar")
    script = re.sub(r"\binit\(\);\s*$", "", script)
    result = subprocess.run(
        [node, str(Path(__file__).with_name("audit_frontend_harness.cjs"))],
        input=json.dumps(
            {
                "script": script,
                "dates": (root / "static/js/date-format.js").read_text(encoding="utf-8"),
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
