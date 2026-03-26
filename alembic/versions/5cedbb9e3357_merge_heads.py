"""merge_heads

Revision ID: 5cedbb9e3357
Revises: 7cd0dd6ec316, 20260326_add_file_hash
Create Date: 2026-03-26 14:55:31.441072

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cedbb9e3357'
down_revision: Union[str, None] = ('7cd0dd6ec316', '20260326_add_file_hash')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
