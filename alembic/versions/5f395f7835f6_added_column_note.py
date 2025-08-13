"""added column note

Revision ID: 5f395f7835f6
Revises: da0483da8f95
Create Date: 2025-08-12 14:45:35.536086

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f395f7835f6'
down_revision: Union[str, None] = 'da0483da8f95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('cameras', sa.Column('note', sa.String(), nullable=True))
    op.add_column('nvr', sa.Column('note', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('cameras', 'note')
    op.drop_column('nvr', 'note')
