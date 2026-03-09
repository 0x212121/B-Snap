"""Add job_execution_logs table

Revision ID: 20260309_add_job_execution_logs
Revises: 20260309_add_notifications_table
create_date: 2026-03-09

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '20260309_add_job_execution_logs'
down_revision = '20260309_add_notifications_table'
branch_labels = None
depends_on = None


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


def upgrade():
    if not table_exists('job_execution_logs'):
        op.create_table(
            'job_execution_logs',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('job_id', sa.String(100), nullable=False),
            sa.Column('job_name', sa.String(200), nullable=False),
            sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('duration_ms', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('records_processed', sa.Integer(), default=0),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        
        # Create indexes
        op.create_index('idx_job_execution_job_id', 'job_execution_logs', ['job_id'])
        op.create_index('idx_job_execution_status', 'job_execution_logs', ['status'])
        op.create_index('idx_job_execution_started_at', 'job_execution_logs', ['started_at'])
        op.create_index('idx_job_status_time', 'job_execution_logs', ['job_id', 'status', 'started_at'])


def downgrade():
    if index_exists('job_execution_logs', 'idx_job_status_time'):
        op.drop_index('idx_job_status_time', table_name='job_execution_logs')
    if index_exists('job_execution_logs', 'idx_job_execution_started_at'):
        op.drop_index('idx_job_execution_started_at', table_name='job_execution_logs')
    if index_exists('job_execution_logs', 'idx_job_execution_status'):
        op.drop_index('idx_job_execution_status', table_name='job_execution_logs')
    if index_exists('job_execution_logs', 'idx_job_execution_job_id'):
        op.drop_index('idx_job_execution_job_id', table_name='job_execution_logs')
    if table_exists('job_execution_logs'):
        op.drop_table('job_execution_logs')
