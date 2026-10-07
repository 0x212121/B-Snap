"""Check startup and documentation behavior in a clean container-like filesystem."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from pathlib import Path

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.static_assets import mount_documentation


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override SQLite schema fixture: these checks do not run application startup."""


def test_missing_documentation_does_not_prevent_startup(tmp_path: Path) -> None:
    app = FastAPI()
    mount_documentation(app, tmp_path)
    with TestClient(app) as client:
        assert client.get("/documentation/index.html").status_code == 404


def test_documentation_is_served_from_project_root(tmp_path: Path, monkeypatch) -> None:
    directory = tmp_path / "docs" / "build" / "html"
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("<html>Bundled documentation</html>")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    app = FastAPI()
    mount_documentation(app, tmp_path)
    with TestClient(app) as client:
        response = client.get("/documentation/index.html")
        assert response.status_code == 200
        assert "Bundled documentation" in response.text
        assert client.get("/documentation/../../outside.txt").status_code == 404


def test_web_app_imports_without_generated_docs_or_deployment_env(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(
        root / "app",
        tmp_path / "app",
        ignore=shutil.ignore_patterns("tests", "__pycache__", "*.exe", "logs", "*.db"),
    )
    (tmp_path / "static").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    env.update(
        PYTHONPATH=str(tmp_path),
        DATABASE_URL="postgresql+psycopg2://image_check@127.0.0.1:1/image_check",
        SECRET_KEY="image-build-check",
        ENVIRONMENT="testing",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.main; print('Web import succeeded')"],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Web import succeeded" in result.stdout
