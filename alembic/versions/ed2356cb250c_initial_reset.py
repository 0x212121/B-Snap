"""initial reset

Revision ID: ed2356cb250c
Revises: 
Create Date: 2025-07-25 04:57:31.435988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ed2356cb250c'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('snapshots', sa.Column('is_tampered', sa.Boolean(), nullable=True, server_default=sa.text('false')))
    op.add_column('snapshots', sa.Column('tamper_reason', sa.String(length=255), nullable=True))
    op.add_column('snapshots', sa.Column('blur_score', sa.Float(), nullable=True))
    op.add_column('snapshots', sa.Column('entropy_score', sa.Float(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('snapshots', 'entropy_score')
    op.drop_column('snapshots', 'blur_score')
    op.drop_column('snapshots', 'tamper_reason')
    op.drop_column('snapshots', 'is_tampered')

