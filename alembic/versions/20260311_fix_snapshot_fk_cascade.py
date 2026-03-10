"""Fix snapshot foreign key cascade

Revision ID: 20260311_fix_snapshot_fk_cascade
Revises: 20260311_add_orphaned_files
Create Date: 2026-03-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260311_fix_snapshot_fk_cascade'
down_revision: Union[str, None] = '20260311_add_orphaned_files'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def constraint_exists(table_name: str, constraint_name: str) -> bool:
    """Check if foreign key constraint exists."""
    bind = op.get_bind()
    inspector = inspect(bind)
    for fk in inspector.get_foreign_keys(table_name):
        if fk['name'] == constraint_name:
            return True
    return False


def upgrade() -> None:
    # Drop existing foreign key constraint if it has cascade delete
    # PostgreSQL constraint name for snapshots.camera_id -> cameras.id
    bind = op.get_bind()
    inspector = inspect(bind)
    
    # Find the foreign key constraint
    fks = inspector.get_foreign_keys('snapshots')
    for fk in fks:
        if fk['referred_table'] == 'cameras' and 'camera_id' in fk['constrained_columns']:
            # Drop the existing FK
            op.drop_constraint(fk['name'], 'snapshots', type_='foreignkey')
            
            # Recreate with SET NULL (no cascade delete)
            op.create_foreign_key(
                None,  # Let Alembic generate name
                'snapshots',
                'cameras',
                ['camera_id'],
                ['id'],
                ondelete='SET NULL'
            )
            break


def downgrade() -> None:
    # Reverse: add back cascade delete (not recommended but for rollback)
    bind = op.get_bind()
    inspector = inspect(bind)
    
    fks = inspector.get_foreign_keys('snapshots')
    for fk in fks:
        if fk['referred_table'] == 'cameras' and 'camera_id' in fk['constrained_columns']:
            op.drop_constraint(fk['name'], 'snapshots', type_='foreignkey')
            
            # Recreate with CASCADE
            op.create_foreign_key(
                None,
                'snapshots',
                'cameras',
                ['camera_id'],
                ['id'],
                ondelete='CASCADE'
            )
            break
