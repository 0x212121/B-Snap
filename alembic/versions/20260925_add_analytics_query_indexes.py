"""Add indexes for analytics date-range queries.

Revision ID: 20260925_analytics_indexes
Revises: 20260924_camera_multiple_groups
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260925_analytics_indexes"
down_revision: Union[str, None] = "20260924_camera_multiple_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_camera_daily_stats_date",
        "camera_daily_stats",
        ["date"],
        unique=False,
    )
    op.create_index(
        "ix_camera_status_change_log_changed_at",
        "camera_status_change_log",
        ["changed_at"],
        unique=False,
    )
    op.create_index(
        "ix_camera_email_notification_logs_sent_at",
        "camera_email_notification_logs",
        ["sent_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_camera_email_notification_logs_sent_at", table_name="camera_email_notification_logs")
    op.drop_index("ix_camera_status_change_log_changed_at", table_name="camera_status_change_log")
    op.drop_index("ix_camera_daily_stats_date", table_name="camera_daily_stats")
