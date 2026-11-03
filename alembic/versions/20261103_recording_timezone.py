"""Upgrade installations created before pending cron timezone tracking was added."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261103_recording_timezone"
down_revision = "20261103_recording_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Tolerate both early and later versions of the initial archive migration.
        op.execute(
            "ALTER TABLE recording_schedules ADD COLUMN IF NOT EXISTS next_run_timezone VARCHAR(100)"
        )
    elif "next_run_timezone" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("recording_schedules")
    }:
        op.add_column("recording_schedules", sa.Column("next_run_timezone", sa.String(100)))


def downgrade() -> None:
    op.drop_column("recording_schedules", "next_run_timezone")
