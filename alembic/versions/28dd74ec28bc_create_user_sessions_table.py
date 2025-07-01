"""create user_sessions table

Revision ID: 28dd74ec28bc
Revises: ddcd10e6480d
Create Date: 2025-07-01 14:06:26.143824

"""
import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '28dd74ec28bc'
down_revision: Union[str, None] = 'ddcd10e6480d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.String(length=128), nullable=False, unique=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime, default=datetime.datetime.utc, nullable=False),
        sa.Column('last_seen', sa.DateTime, default=datetime.datetime.utc, nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('user_sessions')
