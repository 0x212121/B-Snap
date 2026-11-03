"""Add independent archive schedules and camera recording leases."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261103_recording_schedules"
down_revision = "20261103_wa_phone_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_schedules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "camera_id",
            sa.String(36),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("clip_seconds", sa.Integer(), nullable=False),
        sa.Column("segment_seconds", sa.Integer(), nullable=False),
        sa.Column("weekdays", sa.JSON(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recording_schedules_camera_id", "recording_schedules", ["camera_id"])
    op.create_table(
        "camera_recording_states",
        sa.Column(
            "camera_id",
            sa.String(36),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("schedule_id", sa.String(36)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_recorded_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("camera_recording_states")
    op.drop_index("ix_recording_schedules_camera_id", table_name="recording_schedules")
    op.drop_table("recording_schedules")
