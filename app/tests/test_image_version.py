"""Release version checks for the shared Docker image."""

from __future__ import annotations

import re
import tomllib

from pathlib import Path

import pytest

from scripts.check_image_version import validate_image_version


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override SQLite setup: version validation reads source files only."""


def test_default_release_versions_match() -> None:
    with Path("pyproject.toml").open("rb") as source:
        version = tomllib.load(source)["project"]["version"]
    validate_image_version(version)
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert re.search(rf"^ARG APP_VERSION={re.escape(version)}$", dockerfile, re.MULTILINE)
    yaml = pytest.importorskip("yaml", reason="Compose version check uses optional PyYAML")
    services = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert services["b-snap"]["image"].endswith(f":v{version}}}")


@pytest.mark.parametrize(("label", "web"), [("9.0.0", "2.4.0"), ("2.4.0", "9.0.0")])
def test_inconsistent_version_rejects_build(label: str, web: str, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.4.0"\n', encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "version.py").write_text(f'__version__ = "{web}"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must match"):
        validate_image_version(label, tmp_path)
