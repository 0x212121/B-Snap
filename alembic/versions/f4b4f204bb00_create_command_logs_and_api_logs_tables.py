"""create command_logs and api_logs tables

Revision ID: f4b4f204bb00
Revises: d6001ff7c550
Create Date: 2025-09-25 16:26:52.988176

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b4f204bb00'
down_revision: Union[str, None] = 'd6001ff7c550'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "command_logs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("user_id", sa.String, index=True),
        sa.Column("command", sa.String, index=True),
        sa.Column("source", sa.String, server_default="whatsapp", index=True),
    )

    op.create_table(
        "api_logs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("user_id", sa.String, index=True),
        sa.Column("endpoint", sa.String, index=True),
        sa.Column("method", sa.String, index=True),
        sa.Column("status_code", sa.Integer, index=True),
        sa.Column("source", sa.String, server_default="web", index=True),
    )


def downgrade() -> None:
    op.drop_table("api_logs")
    op.drop_table("command_logs")
