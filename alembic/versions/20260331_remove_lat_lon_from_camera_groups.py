"""Remove latitude and longitude from camera_groups

Revision ID: 20260331_remove_lat_lon
Revises: 20260330_remove_all_group
Create Date: 2026-03-31 10:08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260331_remove_lat_lon'
down_revision: Union[str, None] = '20260330_remove_all_group'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Drop latitude and longitude columns from camera_groups table
    op.drop_column('camera_groups', 'latitude')
    op.drop_column('camera_groups', 'longitude')


def downgrade():
    # Add back latitude and longitude columns
    op.add_column('camera_groups', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('camera_groups', sa.Column('longitude', sa.Float(), nullable=True))
