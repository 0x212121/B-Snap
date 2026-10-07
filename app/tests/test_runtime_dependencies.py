"""Regression checks for production imports omitted from Docker dependencies."""

from __future__ import annotations

import tomllib

from pathlib import Path

import pytest

from packaging.requirements import Requirement

from scripts import check_runtime_imports


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full SQLite schema creation: dependency checks never access the database."""


def test_httpx_is_a_compatible_runtime_dependency() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = {req.name: req for req in map(Requirement, project["dependencies"])}
    pinned = {
        req.name: req
        for req in map(
            Requirement,
            (
                line
                for line in Path("requirements.txt").read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ),
        )
    }
    assert "httpx" in runtime
    pin = next(iter(pinned["httpx"].specifier))
    assert pin.operator == "=="
    assert pin.version in runtime["httpx"].specifier


def test_build_check_reports_missing_httpx_without_importing_application(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    (app / "main.py").write_text(
        "import httpx\nimport os\nfrom app import routes\nraise AssertionError('Do not execute')\n"
    )
    monkeypatch.setattr(check_runtime_imports.importlib.util, "find_spec", lambda _: None)
    with pytest.raises(RuntimeError, match="Missing application runtime modules: httpx"):
        check_runtime_imports.validate_runtime_imports(tmp_path)


def test_build_check_skips_tests_and_optional_function_imports(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "tests").mkdir(parents=True)
    (app / "main.py").write_text("import httpx\ndef optional():\n    import optional_export\n")
    (app / "tests" / "test_example.py").write_text("import pytest\n")
    assert check_runtime_imports.application_imports(tmp_path) == {"httpx"}
    monkeypatch.setattr(check_runtime_imports.importlib.util, "find_spec", lambda _: object())
    check_runtime_imports.validate_runtime_imports(tmp_path)
