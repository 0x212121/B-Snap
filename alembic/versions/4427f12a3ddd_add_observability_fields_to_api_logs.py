"""Add observability fields to api_logs

Revision ID: 4427f12a3ddd
Revises: 300b57036bb7
Create Date: 2025-10-09 13:58:41.515214
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4427f12a3ddd"
down_revision: Union[str, None] = "300b57036bb7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("api_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("duration_ms", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("ip_address", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("user_agent", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("error_message", sa.String(length=1024), nullable=True))

    # Optional: jika database besar, index bisa membantu query observability
    op.create_index("ix_api_logs_ip_address", "api_logs", ["ip_address"], unique=False)
    op.create_index("ix_api_logs_duration_ms", "api_logs", ["duration_ms"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_api_logs_duration_ms", table_name="api_logs")
    op.drop_index("ix_api_logs_ip_address", table_name="api_logs")
    with op.batch_alter_table("api_logs", schema=None) as batch_op:
        batch_op.drop_column("error_message")
        batch_op.drop_column("user_agent")
        batch_op.drop_column("ip_address")
        batch_op.drop_column("duration_ms")
