"""Add snapshot URL

Revision ID: cf721a00b9a8
Revises: eb9923e83d68
Create Date: 2025-08-15 16:26:55.792687

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf721a00b9a8'
down_revision: Union[str, None] = 'eb9923e83d68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'cameras',
        sa.Column('snapshot_url', sa.String(length=255), nullable=True)
    )


def downgrade():
    op.drop_column('cameras', 'snapshot_url')