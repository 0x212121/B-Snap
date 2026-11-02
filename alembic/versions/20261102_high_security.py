"""Add shared auth throttling and group-scoped health-check progress.

Revision ID: 20261102_high_security
Revises: 20261012_email_retry_expiry
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261102_high_security"
down_revision = "20261012_email_retry_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve the global progress row and add isolated scoped progress."""
    op.create_table(
        "auth_rate_limits",
        sa.Column("bucket", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_rate_limits_window_end", "auth_rate_limits", ["window_end"])
    op.add_column("health_check_status", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_health_check_status_group",
        "health_check_status",
        "camera_groups",
        ["group_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_health_check_status_group", "health_check_status", ["group_id"])


def downgrade() -> None:
    """Remove scoped state while preserving the original global tracker."""
    op.execute(sa.text("DELETE FROM health_check_status WHERE group_id IS NOT NULL"))
    op.drop_constraint("uq_health_check_status_group", "health_check_status", type_="unique")
    op.drop_constraint("fk_health_check_status_group", "health_check_status", type_="foreignkey")
    op.drop_column("health_check_status", "group_id")
    op.drop_index("ix_auth_rate_limits_window_end", table_name="auth_rate_limits")
    op.drop_table("auth_rate_limits")
