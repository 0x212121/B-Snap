"""add_alert_cooldown

Revision ID: 20260330_alert_cooldown
Revises: 20260330_circuit_breaker
Create Date: 2026-03-30 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '20260330_alert_cooldown'
down_revision: Union[str, None] = '20260330_circuit_breaker'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add alert cooldown fields to camera_health table."""
    # Add cooldown tracking for tamper alerts
    op.add_column('camera_health', sa.Column('alert_cooldown_until', 
                                             sa.DateTime(timezone=True), 
                                             nullable=True))
    op.add_column('camera_health', sa.Column('last_alert_reason', 
                                             sa.String(), 
                                             nullable=True))
    
    # Index for performance
    op.create_index(op.f('ix_camera_health_alert_cooldown_until'), 
                    'camera_health', ['alert_cooldown_until'], unique=False)
    
    # Comments
    op.execute("""
        COMMENT ON COLUMN camera_health.alert_cooldown_until IS 
        'Timestamp when next alert can be sent (prevents flooding)';
    """)
    op.execute("""
        COMMENT ON COLUMN camera_health.last_alert_reason IS 
        'Reason for last alert sent (for deduplication)';
    """)


def downgrade() -> None:
    """Remove alert cooldown fields."""
    op.drop_index(op.f('ix_camera_health_alert_cooldown_until'), table_name='camera_health')
    op.drop_column('camera_health', 'last_alert_reason')
    op.drop_column('camera_health', 'alert_cooldown_until')
