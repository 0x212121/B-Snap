"""Add notifications table for toast notifications.

Revision ID: 20260309_add_notifications_table
Revises: 20250306_remember_me
Create Date: 2026-03-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '20260309_add_notifications_table'
down_revision = '20250306_remember_me'
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
    """Create notifications table."""
    if not table_exists('notifications'):
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('type', sa.String(length=20), nullable=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('camera_id', sa.Integer(), nullable=True),
            sa.Column('is_read', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
    if not index_exists('notifications', 'ix_notifications_id'):
        op.create_index(op.f('ix_notifications_id'), 'notifications', ['id'], unique=False)


def downgrade():
    """Drop notifications table."""
    if index_exists('notifications', 'ix_notifications_id'):
        op.drop_index(op.f('ix_notifications_id'), table_name='notifications')
    if table_exists('notifications'):
        op.drop_table('notifications')
