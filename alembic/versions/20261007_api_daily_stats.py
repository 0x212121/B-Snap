"""Retain daily API totals independently of detailed request logs.

Revision ID: 20261007_api_daily_stats
Revises: 20261006_record_mounts
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261007_api_daily_stats"
down_revision = "20261006_record_mounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_daily_stats",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("date", "timezone"),
    )


def downgrade() -> None:
    op.drop_table("api_daily_stats")
