"""
Add storage monitoring tables

Revision ID: 20260306_add_storage_monitoring
Revises: e8c96ec70560
Create Date: 2026-03-06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260306_add_storage_monitoring'
down_revision: Union[str, None] = 'e8c96ec70560'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name):
    """Check if table exists"""
    conn = op.get_bind()
    inspector = inspect(conn)
    return table_name in inspector.get_table_names()


def index_exists(table_name, index_name):
    """Check if index exists"""
    conn = op.get_bind()
    inspector = inspect(conn)
    indexes = inspector.get_indexes(table_name)
    return any(idx['name'] == index_name for idx in indexes)


def upgrade():
    # Create storage_metrics table if not exists
    if not table_exists('storage_metrics'):
        op.create_table(
            'storage_metrics',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
            sa.Column('total_bytes', sa.BigInteger(), nullable=False),
            sa.Column('used_bytes', sa.BigInteger(), nullable=False),
            sa.Column('free_bytes', sa.BigInteger(), nullable=False),
            sa.Column('usage_percent', sa.Float(), nullable=False),
            sa.Column('snapshots_bytes', sa.BigInteger(), server_default='0'),
            sa.Column('videos_bytes', sa.BigInteger(), server_default='0'),
            sa.Column('logs_bytes', sa.BigInteger(), server_default='0'),
            sa.Column('other_bytes', sa.BigInteger(), server_default='0'),
            sa.Column('daily_growth_rate', sa.Float(), server_default='0'),
            sa.Column('days_until_full', sa.Float(), server_default='0'),
            sa.Column('alert_level', sa.String(20), server_default='normal'),
            sa.Column('alert_message', sa.String(500), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
    
    # Create index on timestamp for faster queries
    if table_exists('storage_metrics') and not index_exists('storage_metrics', 'ix_storage_metrics_timestamp'):
        op.create_index(
            'ix_storage_metrics_timestamp',
            'storage_metrics',
            ['timestamp']
        )
    
    # Create storage_alerts table if not exists
    if not table_exists('storage_alerts'):
        op.create_table(
            'storage_alerts',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
            sa.Column('level', sa.String(20), nullable=False),
            sa.Column('message', sa.String(500), nullable=False),
            sa.Column('usage_percent', sa.Float(), nullable=False),
            sa.Column('free_gb', sa.Float(), nullable=False),
            sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('resolved_by', sa.String(100), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
    
    # Create index on timestamp for alerts
    if table_exists('storage_alerts') and not index_exists('storage_alerts', 'ix_storage_alerts_timestamp'):
        op.create_index(
            'ix_storage_alerts_timestamp',
            'storage_alerts',
            ['timestamp']
        )


def downgrade():
    # Drop indexes first (if exist)
    if table_exists('storage_alerts') and index_exists('storage_alerts', 'ix_storage_alerts_timestamp'):
        op.drop_index('ix_storage_alerts_timestamp', table_name='storage_alerts')
    
    if table_exists('storage_metrics') and index_exists('storage_metrics', 'ix_storage_metrics_timestamp'):
        op.drop_index('ix_storage_metrics_timestamp', table_name='storage_metrics')
    
    # Drop tables (if exist)
    if table_exists('storage_alerts'):
        op.drop_table('storage_alerts')
    
    if table_exists('storage_metrics'):
        op.drop_table('storage_metrics')
