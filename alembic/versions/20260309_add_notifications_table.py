"""Add notifications table for toast notifications.

Revision ID: 20260309_add_notifications_table
Revises: 20250306_remember_me
Create Date: 2026-03-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260309_add_notifications_table'
down_revision = '20250306_remember_me'
branch_labels = None
depends_on = None


def upgrade():
    """Create notifications table."""
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
    op.create_index(op.f('ix_notifications_id'), 'notifications', ['id'], unique=False)


def downgrade():
    """Drop notifications table."""
    op.drop_index(op.f('ix_notifications_id'), table_name='notifications')
    op.drop_table('notifications')
