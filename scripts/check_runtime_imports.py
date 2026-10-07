"""Check mandatory application import modules without starting services or loading .env."""

from __future__ import annotations

import ast
import importlib.util
import sys

from pathlib import Path


def application_imports(root: Path) -> set[str]:
    """Collect top-level external imports, excluding tests and optional local imports."""
    modules: set[str] = set()
    for source in (root / "app").rglob("*.py"):
        if "tests" in source.relative_to(root / "app").parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules - set(sys.stdlib_module_names) - {"app"}


def validate_runtime_imports(root: Path | None = None) -> None:
    """Fail the build if a mandatory import cannot be found in the runtime environment."""
    root = root or Path(__file__).resolve().parents[1]
    missing = sorted(
        module for module in application_imports(root) if importlib.util.find_spec(module) is None
    )
    if missing:
        raise RuntimeError(f"Missing application runtime modules: {', '.join(missing)}")


if __name__ == "__main__":
    validate_runtime_imports()
