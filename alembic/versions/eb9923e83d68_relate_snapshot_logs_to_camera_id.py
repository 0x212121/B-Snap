"""relate_snapshot_logs_to_camera_id

Revision ID: eb9923e83d68
Revises: 5f395f7835f6
Create Date: 2025-08-13 15:34:45.607340

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb9923e83d68'
down_revision: Union[str, None] = '5f395f7835f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tahap 1: Tambah kolom camera_id, buat nullable sementara
    op.add_column('snapshot_logs', sa.Column('camera_id', sa.String(length=36), nullable=True))
    
    print("Kolom 'camera_id' ditambahkan. Memulai proses migrasi data...")

    # Tahap 2: Migrasi data, isi camera_id berdasarkan camera_name
    cameras_table = sa.table('cameras',
        sa.column('id', sa.String),
        sa.column('hostname', sa.String)
    )
    snapshot_logs_table = sa.table('snapshot_logs',
        sa.column('camera_name', sa.String),
        sa.column('camera_id', sa.String)
    )
    update_stmt = (
        snapshot_logs_table.update()
        .where(cameras_table.c.hostname == snapshot_logs_table.c.camera_name)
        .values(camera_id=cameras_table.c.id)
    )
    op.execute(update_stmt)
    
    print("Migrasi data selesai.")

    # --- PERUBAHAN DI SINI ---
    # Tahap 2.5: Hapus baris-baris yatim yang camera_id nya masih NULL
    delete_stmt = snapshot_logs_table.delete().where(snapshot_logs_table.c.camera_id == None)
    op.execute(delete_stmt)
    
    print("Data log yatim (orphaned logs) telah dihapus.")
    # -------------------------

    # Tahap 3: Setelah data bersih, ubah kolom camera_id menjadi NOT NULL
    op.alter_column('snapshot_logs', 'camera_id', nullable=False)
    
    print("Kolom 'camera_id' diubah menjadi NOT NULL.")

    # Tahap 4 & 5: Lanjutkan seperti sebelumnya
    op.drop_constraint('snapshot_logs_camera_name_fkey', 'snapshot_logs', type_='foreignkey')
    print("Foreign key lama ke 'cameras.hostname' telah dihapus.")

    op.create_foreign_key(
        'snapshot_logs_camera_id_fkey',
        'snapshot_logs',
        'cameras',
        ['camera_id'],
        ['id']
    )
    print("Foreign key baru ke 'cameras.id' telah dibuat. Migrasi 'upgrade' selesai.")

def downgrade() -> None:
    # ### Langkah downgrade dibalik dari upgrade ###

    # ### Tahap 1: Hapus Foreign Key yang baru ###
    op.drop_constraint(
        'snapshot_logs_camera_id_fkey',
        'snapshot_logs',
        type_='foreignkey'
    )
    
    print("Foreign key baru ke 'cameras.id' telah dihapus.")

    # ### Tahap 2: Buat kembali Foreign Key yang lama ###
    # Kita tambahkan ON UPDATE CASCADE di sini sebagai fallback yang aman
    op.create_foreign_key(
        'snapshot_logs_camera_name_fkey',
        'snapshot_logs',
        'cameras',
        ['camera_name'],
        ['hostname'],
        onupdate='CASCADE' 
    )

    print("Foreign key lama ke 'cameras.hostname' telah dibuat kembali.")

    # ### Tahap 3: Hapus kolom camera_id ###
    op.drop_column('snapshot_logs', 'camera_id')
    
    print("Kolom 'camera_id' telah dihapus. Migrasi 'downgrade' selesai.")
