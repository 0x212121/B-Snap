"""add_notification_circuit_breaker

Revision ID: 20260330_circuit_breaker
Revises: 20260328_dual_table
Create Date: 2026-03-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260330_circuit_breaker'
down_revision: Union[str, None] = '20260328_dual_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add notification circuit breaker fields to cameras table."""
    # Add columns for email notification circuit breaker
    op.add_column('cameras', sa.Column('notification_fail_count', sa.Integer(), 
                                       server_default='0', nullable=False))
    op.add_column('cameras', sa.Column('notification_suppressed_until', 
                                       sa.DateTime(timezone=True), nullable=True))
    op.add_column('cameras', sa.Column('last_notification_at', 
                                       sa.DateTime(timezone=True), nullable=True))
    
    # Add indexes for performance
    op.create_index(op.f('ix_cameras_notification_suppressed_until'), 
                    'cameras', ['notification_suppressed_until'], unique=False)
    
    # Add comment explaining the fields
    op.execute("""
        COMMENT ON COLUMN cameras.notification_fail_count IS 
        'Circuit breaker: consecutive notification failures (max 3 before suppression)';
    """)
    op.execute("""
        COMMENT ON COLUMN cameras.notification_suppressed_until IS 
        'Circuit breaker: timestamp when notification suppression ends';
    """)
    op.execute("""
        COMMENT ON COLUMN cameras.last_notification_at IS 
        'Timestamp of last notification attempt for deduplication';
    """)


def downgrade() -> None:
    """Remove notification circuit breaker fields."""
    op.drop_index(op.f('ix_cameras_notification_suppressed_until'), table_name='cameras')
    op.drop_column('cameras', 'last_notification_at')
    op.drop_column('cameras', 'notification_suppressed_until')
    op.drop_column('cameras', 'notification_fail_count')
