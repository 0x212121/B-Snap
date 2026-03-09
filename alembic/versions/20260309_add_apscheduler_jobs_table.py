"""Add APScheduler jobs table

Revision ID: 20260309_add_apscheduler_jobs
Revises: 20260309_add_job_execution_logs
Create Date: 2026-03-09 11:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260309_add_apscheduler_jobs'
down_revision: Union[str, None] = '20260309_add_job_execution_logs'
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
    # Create apscheduler_jobs table for persistent job storage (if not exists)
    if not table_exists('apscheduler_jobs'):
        op.create_table(
            'apscheduler_jobs',
            sa.Column('id', sa.String(191), nullable=False),
            sa.Column('next_run_time', sa.Float(precision=25), nullable=True),
            sa.Column('job_state', sa.LargeBinary(), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
    
    # Create index if not exists
    if not index_exists('apscheduler_jobs', 'ix_apscheduler_jobs_next_run_time'):
        op.create_index('ix_apscheduler_jobs_next_run_time', 'apscheduler_jobs', ['next_run_time'])


def downgrade() -> None:
    if index_exists('apscheduler_jobs', 'ix_apscheduler_jobs_next_run_time'):
        op.drop_index('ix_apscheduler_jobs_next_run_time', table_name='apscheduler_jobs')
    if table_exists('apscheduler_jobs'):
        op.drop_table('apscheduler_jobs')
