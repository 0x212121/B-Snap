"""Add WhatsApp bot delivery logs.

Revision ID: 20260930_wa_ops
Revises: fe1c28ede362
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260930_wa_ops"
down_revision: Union[str, tuple[str, str], None] = ("20260925_analytics_indexes", "fe1c28ede362")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM configurations WHERE key = 'wa_bot_operations'"))
    op.create_table(
        "whatsapp_message_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("phone_number", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("command", sa.String(120), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    for column in ("timestamp", "phone_number", "direction", "status"):
        op.create_index(f"ix_whatsapp_message_logs_{column}", "whatsapp_message_logs", [column])

def downgrade() -> None:
    op.drop_table("whatsapp_message_logs")
