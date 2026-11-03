"""Manual legacy diagnostic, excluded from automated test discovery.

Run from the repository root. This may access or modify the configured deployment.
Importing this module never executes the diagnostic.
"""

from __future__ import annotations


def run_diagnostic() -> None:
    """Execute the original diagnostic only after explicit command-line opt-in."""
    #!/usr/bin/env python
    import sys
    import os

    os.chdir("/")
    sys.path.insert(0, "/app")

    from alembic.config import Config
    from alembic import command
    import traceback

    try:
        alembic_cfg = Config("/alembic.ini")
        command.upgrade(alembic_cfg, "head")
        print("Migration completed successfully!")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-operations", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_operations:
        parser.error("requires --allow-live-operations; review the script before using it")
    run_diagnostic()
