"""Allow individual cameras to skip scheduled snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "20261117_skip_snapshot"
down_revision = "20261103_recording_timezone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column(
            "skip_scheduled_snapshot", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("cameras", "skip_scheduled_snapshot")
