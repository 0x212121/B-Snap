"""Add whitelist group id

Revision ID: d6001ff7c550
Revises: cf721a00b9a8
Create Date: 2025-08-15 23:07:29.353197

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6001ff7c550'
down_revision: Union[str, None] = 'cf721a00b9a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('whatsapp_whitelist', sa.Column('group_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_whatsapp_whitelist_group_id',
        'whatsapp_whitelist', 'camera_groups',
        ['group_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    op.drop_constraint('fk_whatsapp_whitelist_group_id', 'whatsapp_whitelist', type_='foreignkey')
    op.drop_column('whatsapp_whitelist', 'group_id')
