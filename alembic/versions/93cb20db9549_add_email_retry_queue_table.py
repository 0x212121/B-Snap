"""Add email_retry_queue table

Revision ID: 93cb20db9549
Revises: 823ab92ff506
Create Date: 2025-10-24 13:50:29.364482

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93cb20db9549'
down_revision: Union[str, None] = '823ab92ff506'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        "email_retry_queue",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("camera_id", sa.String(length=64), sa.ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),  # "tamper" / "recovery"
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # Optional: index untuk mempercepat query scheduler
    op.create_index(
        "ix_email_retry_queue_pending",
        "email_retry_queue",
        ["sent", "attempts", "next_retry_at"]
    )


def downgrade():
    op.drop_index("ix_email_retry_queue_pending", table_name="email_retry_queue")
    op.drop_table("email_retry_queue")