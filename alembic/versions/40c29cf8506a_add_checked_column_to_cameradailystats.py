"""Add checked column to CameraDailyStats

Revision ID: 40c29cf8506a
Revises: 
Create Date: 2025-07-04 14:56:47.699851
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision: str = '40c29cf8506a'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Tambah kolom 'checked' (nullable=True agar lolos SQLite)
    op.add_column(
        'camera_daily_stats',
        sa.Column('checked', sa.DateTime(timezone=True), nullable=True)
    )

    # Step 2: Isi semua record lama dengan timestamp sekarang
    conn = op.get_bind()
    now = datetime.utcnow().isoformat()
    conn.execute(sa.text(f"UPDATE camera_daily_stats SET checked = '{now}'"))

    # Step 3: (Optional) Biarkan kolom tetap nullable
    # SQLite tidak bisa ubah kolom jadi NOT NULL, jadi skip langkah alter_column


def downgrade() -> None:
    op.drop_column('camera_daily_stats', 'checked')
