"""Reject Docker labels that do not match the package and web application versions."""

from __future__ import annotations

import argparse
import ast
import tomllib

from pathlib import Path


def validate_image_version(expected: str, root: Path | None = None) -> None:
    """Check release metadata using source files without importing the application."""
    root = root or Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as source:
        package_version = tomllib.load(source)["project"]["version"]
    tree = ast.parse((root / "app" / "version.py").read_text(encoding="utf-8"))
    web_version = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    )
    if expected != package_version or web_version != package_version:
        raise ValueError(
            f"Image version {expected!r}, package version {package_version!r}, "
            f"and application version {web_version!r} must match"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", help="Version supplied through the APP_VERSION build argument")
    validate_image_version(parser.parse_args().expected)
