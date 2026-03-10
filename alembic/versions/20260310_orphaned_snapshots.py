"""Add orphaned column

Revision ID: 20260310_orphaned_snapshots
Revises: 20260309_add_sla_reports
Create Date: 2026-03-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260310_orphaned_snapshots'
down_revision: Union[str, None] = '20260309_add_sla_reports'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)


def upgrade() -> None:
    if not column_exists('snapshots', 'is_orphaned'):
        op.add_column(
            'snapshots',
            sa.Column('is_orphaned', sa.Boolean(), nullable=True)
        )
        # Update existing rows to False
        op.execute("UPDATE snapshots SET is_orphaned = FALSE WHERE is_orphaned IS NULL")
        # Make column not nullable
        op.alter_column('snapshots', 'is_orphaned', nullable=False, server_default='false')


def downgrade() -> None:
    if column_exists('snapshots', 'is_orphaned'):
        op.drop_column('snapshots', 'is_orphaned')
