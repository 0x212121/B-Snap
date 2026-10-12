"""Record and index pending email notification expiry.

Revision ID: 20261012_email_retry_expiry
Revises: 20261007_email_retry_resources
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261012_email_retry_expiry"
down_revision = "20261007_email_retry_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the cancellation reason and an index for pending-age sweeps."""
    op.add_column(
        "email_retry_queue", sa.Column("cancellation_reason", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_email_retry_pending_age",
        "email_retry_queue",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Remove expiry metadata without deleting notification history."""
    op.drop_index("ix_email_retry_pending_age", table_name="email_retry_queue")
    op.drop_column("email_retry_queue", "cancellation_reason")
