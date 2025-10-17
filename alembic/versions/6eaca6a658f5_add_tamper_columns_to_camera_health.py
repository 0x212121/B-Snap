"""Add tamper columns to camera health

Revision ID: 6eaca6a658f5
Revises: 4427f12a3ddd
Create Date: 2025-10-17 14:32:44.904608

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6eaca6a658f5'
down_revision: Union[str, None] = '4427f12a3ddd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column('camera_health', sa.Column('tamper_status', sa.String(), nullable=False, server_default='normal'))
    op.add_column('camera_health', sa.Column('tamper_reason', sa.String(), nullable=True))
    op.add_column('camera_health', sa.Column('consecutive_tamper', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('camera_health', sa.Column('consecutive_normal', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('camera_health', sa.Column('last_email_sent', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('camera_health', 'last_email_sent')
    op.drop_column('camera_health', 'consecutive_normal')
    op.drop_column('camera_health', 'consecutive_tamper')
    op.drop_column('camera_health', 'tamper_reason')
    op.drop_column('camera_health', 'tamper_status')