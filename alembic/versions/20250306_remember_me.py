"""Add remember_tokens table for Remember Me functionality

Revision ID: 20250306_remember_me
Revises: 20260306_add_storage_monitoring
Create Date: 2025-03-06 08:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250306_remember_me'
down_revision = '20260306_add_storage_monitoring'
branch_labels = None
depends_on = None


def upgrade():
    # Create remember_tokens table
    op.create_table(
        'remember_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('device_name', sa.String(length=100), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_remember_tokens_id'), 'remember_tokens', ['id'], unique=False)
    op.create_index(op.f('ix_remember_tokens_token_hash'), 'remember_tokens', ['token_hash'], unique=True)
    op.create_index(op.f('ix_remember_tokens_user_id'), 'remember_tokens', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_remember_tokens_user_id'), table_name='remember_tokens')
    op.drop_index(op.f('ix_remember_tokens_token_hash'), table_name='remember_tokens')
    op.drop_index(op.f('ix_remember_tokens_id'), table_name='remember_tokens')
    op.drop_table('remember_tokens')
