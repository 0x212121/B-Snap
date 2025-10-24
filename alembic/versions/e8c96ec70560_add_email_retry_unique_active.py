"""Add email_retry_unique_active

Revision ID: e8c96ec70560
Revises: 93cb20db9549
Create Date: 2025-10-24 15:13:45.923379

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8c96ec70560'
down_revision: Union[str, None] = '93cb20db9549'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Unik hanya untuk baris yang belum sent (active queue)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_email_retry_active
        ON email_retry_queue (camera_id, type)
        WHERE sent = false
    """)
    
def downgrade():
    op.execute("DROP INDEX IF EXISTS ux_email_retry_active")
