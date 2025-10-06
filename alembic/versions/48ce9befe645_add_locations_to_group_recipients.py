"""add locations to group_recipients

Revision ID: 48ce9befe645
Revises: 300b57036bb7
Create Date: 2025-10-03 09:17:48.007256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48ce9befe645'
down_revision: Union[str, None] = '300b57036bb7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('group_recipients', sa.Column('locations', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('group_recipients', 'locations')
