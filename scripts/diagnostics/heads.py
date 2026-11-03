"""Manual legacy diagnostic, excluded from automated test discovery.

Run from the repository root. This may access or modify the configured deployment.
Importing this module never executes the diagnostic.
"""

from __future__ import annotations


def run_diagnostic() -> None:
    """Execute the original diagnostic only after explicit command-line opt-in."""
    #!/usr/bin/env python
    import os

    os.chdir("/")

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    alembic_cfg = Config("/alembic.ini")
    script = ScriptDirectory.from_config(alembic_cfg)

    print("Heads:", script.get_heads())
    print("Current head:", script.get_current_head())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live-operations", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_operations:
        parser.error("requires --allow-live-operations; review the script before using it")
    run_diagnostic()
