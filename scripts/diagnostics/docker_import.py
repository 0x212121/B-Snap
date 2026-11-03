"""Manual legacy diagnostic, excluded from automated test discovery.

Run from the repository root. This may access or modify the configured deployment.
Importing this module never executes the diagnostic.
"""

from __future__ import annotations


def run_diagnostic() -> None:
    """Execute the original diagnostic only after explicit command-line opt-in."""
    #!/usr/bin/env python3
    """Test script to verify all imports work correctly in Docker."""

    import sys
    import traceback

    def test_import(module_name, item_name=None):
        """Test importing a module."""
        try:
            if item_name:
                exec(f"from {module_name} import {item_name}")
            else:
                exec(f"import {module_name}")
            print(f"[OK] {module_name}" + (f".{item_name}" if item_name else ""))
            return True
        except Exception as e:
            print(f"[ERROR] {module_name}" + (f".{item_name}" if item_name else "") + f": {e}")
            traceback.print_exc()
            return False

    print("=" * 60)
    print("Testing Imports")
    print("=" * 60)

    all_ok = True

    # Test models
    all_ok &= test_import("app.models.sla_report", "SLAReport")
    all_ok &= test_import("app.models.sla_report", "ScheduledReport")
    all_ok &= test_import("app.models.camera", "Camera")
    all_ok &= test_import("app.models.user", "User")
    all_ok &= test_import("app.models")

    # Test routes
    all_ok &= test_import("app.routes.health")
    all_ok &= test_import("app.routes.jobs")

    # Test main app
    all_ok &= test_import("app.main", "app")

    print("=" * 60)
    if all_ok:
        print("All imports successful!")
        sys.exit(0)
    else:
        print("Some imports failed!")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-operations", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_operations:
        parser.error("requires --allow-live-operations; review the script before using it")
    run_diagnostic()
