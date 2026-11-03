"""Retire the unused persistent toast inbox without discarding historical rows.

Revision ID: 20261103_retire_inbox
Revises: 20261102_high_security
"""

from __future__ import annotations

from alembic import op

revision = "20261103_retire_inbox"
down_revision = "20261102_high_security"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Archive the legacy inbox; email logs and the WebSocket queue are independent."""
    op.rename_table("notifications", "retired_notifications")


def downgrade() -> None:
    """Restore the legacy table and all historical rows for an application rollback."""
    op.rename_table("retired_notifications", "notifications")
