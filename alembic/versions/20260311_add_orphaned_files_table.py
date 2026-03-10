"""Add orphaned_files table

Revision ID: 20260311_add_orphaned_files
Revises: 20260310_orphaned_snapshots
Create Date: 2026-03-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260311_add_orphaned_files'
down_revision: Union[str, None] = '20260310_orphaned_snapshots'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if table exists in database."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if index exists on table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(idx['name'] == index_name for idx in indexes)


def upgrade() -> None:
    if not table_exists('orphaned_files'):
        op.create_table(
            'orphaned_files',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('file_path', sa.String(500), nullable=False),
            sa.Column('file_size', sa.BigInteger(), nullable=True),
            sa.Column('camera_id', sa.String(36), nullable=True),
            sa.Column('detected_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('status', sa.String(20), nullable=True),
            sa.Column('notes', sa.String(500), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        
        # Create indexes
        op.create_index('idx_orphaned_file_path', 'orphaned_files', ['file_path'], unique=True)
        op.create_index('idx_orphaned_camera_id', 'orphaned_files', ['camera_id'])
        op.create_index('idx_orphaned_status', 'orphaned_files', ['status'])


def downgrade() -> None:
    if index_exists('orphaned_files', 'idx_orphaned_status'):
        op.drop_index('idx_orphaned_status', table_name='orphaned_files')
    if index_exists('orphaned_files', 'idx_orphaned_camera_id'):
        op.drop_index('idx_orphaned_camera_id', table_name='orphaned_files')
    if index_exists('orphaned_files', 'idx_orphaned_file_path'):
        op.drop_index('idx_orphaned_file_path', table_name='orphaned_files')
    
    if table_exists('orphaned_files'):
        op.drop_table('orphaned_files')
