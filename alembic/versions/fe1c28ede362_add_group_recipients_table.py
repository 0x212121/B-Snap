"""add group_recipients table

Revision ID: fe1c28ede362
Revises: f4b4f204bb00
Create Date: 2025-09-26 10:36:56.569314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe1c28ede362'
down_revision: Union[str, None] = 'f4b4f204bb00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.create_table(
        'group_recipients',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('group_id', sa.Integer, sa.ForeignKey('camera_groups.id', ondelete="CASCADE")),
        sa.Column('email', sa.String, nullable=False, index=True),
    )


def downgrade():
    op.drop_table('group_recipients')