"""Index due email retries for bounded ordered batches.

Revision ID: 20261007_email_retry_resources
Revises: 20261007_email_retry
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261007_email_retry_resources"
down_revision = "20261007_email_retry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index pending tasks in the worker's due-time order."""
    op.create_index(
        "ix_email_retry_due",
        "email_retry_queue",
        ["next_retry_at", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Remove the retry batch index."""
    op.drop_index("ix_email_retry_due", table_name="email_retry_queue")
