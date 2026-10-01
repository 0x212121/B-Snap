"""Store GoWA message IDs separately for webhook deduplication.

Revision ID: 20260930_wa_provider_id
Revises: 20260930_wa_ops
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260930_wa_provider_id"
down_revision: Union[str, None] = "20260930_wa_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text(
        "DELETE FROM command_logs "
        "WHERE source = 'whatsapp_webhook' AND command LIKE 'gowa_event:%'"
    ))
    op.add_column(
        "whatsapp_message_logs",
        sa.Column("provider_message_id", sa.String(200), nullable=True),
    )
    op.create_index(
        "ix_whatsapp_message_logs_provider_message_id",
        "whatsapp_message_logs",
        ["provider_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_whatsapp_message_logs_provider_message_id", table_name="whatsapp_message_logs")
    op.drop_column("whatsapp_message_logs", "provider_message_id")
