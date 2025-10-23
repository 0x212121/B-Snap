"""Add reason column to email log

Revision ID: 823ab92ff506
Revises: 6eaca6a658f5
Create Date: 2025-10-23 13:43:40.429700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '823ab92ff506'
down_revision: Union[str, None] = '6eaca6a658f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.add_column(
        'camera_email_notification_logs',
        sa.Column('reason', sa.String(length=64), nullable=True)
    )

def downgrade():
    op.drop_column('camera_email_notification_logs', 'reason')
