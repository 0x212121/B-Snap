"""initial reset

Revision ID: ed2356cb250c
Revises: 
Create Date: 2025-07-25 04:57:31.435988

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = 'ed2356cb250c'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if table exists in database."""
    conn = op.get_bind()
    inspector = inspect(conn)
    return table_name in inspector.get_table_names()


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Upgrade schema."""
    # Skip if snapshots table doesn't exist yet (will be created by Base.metadata.create_all)
    if not table_exists('snapshots'):
        return
    
    # Add columns only if they don't exist
    if not column_exists('snapshots', 'is_tampered'):
        op.add_column('snapshots', sa.Column('is_tampered', sa.Boolean(), nullable=True, server_default=sa.text('false')))
    
    if not column_exists('snapshots', 'tamper_reason'):
        op.add_column('snapshots', sa.Column('tamper_reason', sa.String(length=255), nullable=True))
    
    if not column_exists('snapshots', 'blur_score'):
        op.add_column('snapshots', sa.Column('blur_score', sa.Float(), nullable=True))
    
    if not column_exists('snapshots', 'entropy_score'):
        op.add_column('snapshots', sa.Column('entropy_score', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    if not table_exists('snapshots'):
        return
        
    if column_exists('snapshots', 'entropy_score'):
        op.drop_column('snapshots', 'entropy_score')
    if column_exists('snapshots', 'blur_score'):
        op.drop_column('snapshots', 'blur_score')
    if column_exists('snapshots', 'tamper_reason'):
        op.drop_column('snapshots', 'tamper_reason')
    if column_exists('snapshots', 'is_tampered'):
        op.drop_column('snapshots', 'is_tampered')
