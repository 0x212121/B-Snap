"""Remove latitude and longitude from camera_groups

Revision ID: 20260331_remove_lat_lon
Revises: 20260330_remove_all_group
Create Date: 2026-03-31 10:08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '20260331_remove_lat_lon'
down_revision: Union[str, None] = '20260330_remove_all_group'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)


def upgrade():
    # FIX: Check existence before drop
    if column_exists('camera_groups', 'latitude'):
        op.drop_column('camera_groups', 'latitude')
    if column_exists('camera_groups', 'longitude'):
        op.drop_column('camera_groups', 'longitude')


def downgrade():
    # FIX: Check existence before add
    if not column_exists('camera_groups', 'latitude'):
        op.add_column('camera_groups', sa.Column('latitude', sa.Float(), nullable=True))
    if not column_exists('camera_groups', 'longitude'):
        op.add_column('camera_groups', sa.Column('longitude', sa.Float(), nullable=True))