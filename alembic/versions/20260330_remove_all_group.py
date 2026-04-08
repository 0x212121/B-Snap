"""remove_all_group

Revision ID: 20260330_remove_all_group
Revises: 20260330_alert_cooldown
Create Date: 2026-03-30 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '20260330_remove_all_group'
down_revision: Union[str, None] = '20260330_alert_cooldown'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Remove group "ALL" and set users/cameras/nvr with that group to have no group (NULL).
    Users with NULL group_id now have access to all cameras.
    """
    # Get database connection
    conn = op.get_bind()
    
    # Find the "ALL" group
    result = conn.execute(sa.text("SELECT id FROM camera_groups WHERE name = 'ALL'"))
    all_group = result.fetchone()
    
    if all_group:
        all_group_id = all_group[0]
        
        # FIX 1: Update NVR yang pakai group ALL ke NULL (hindari FK violation)
        conn.execute(
            sa.text("UPDATE nvr SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        print(f"Updated NVR with group_id={all_group_id} to NULL")
        
        # FIX 2: Update cameras yang pakai group ALL ke NULL (hindari FK violation)
        conn.execute(
            sa.text("UPDATE cameras SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        print(f"Updated cameras with group_id={all_group_id} to NULL")
        
        # FIX 3: Update users yang pakai group ALL ke NULL (sudah ada sebelumnya)
        conn.execute(
            sa.text("UPDATE users SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        print(f"Updated users with group_id={all_group_id} to NULL")
        
        # FIX 4: Update whatsapp_whitelist jika ada yang pakai group ALL
        conn.execute(
            sa.text("UPDATE whatsapp_whitelist SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        print(f"Updated whatsapp_whitelist with group_id={all_group_id} to NULL")
        
        # FIX 5: Update group_recipients jika ada yang pakai group ALL  
        conn.execute(
            sa.text("UPDATE group_recipients SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        print(f"Updated group_recipients with group_id={all_group_id} to NULL")
        
        # Baru delete group ALL setelah semua referensi di-set NULL
        conn.execute(
            sa.text("DELETE FROM camera_groups WHERE id = :group_id"),
            {"group_id": all_group_id}
        )
        
        print(f"Removed group 'ALL' (id={all_group_id}) and updated all references to NULL")
    else:
        print("Group 'ALL' not found - nothing to remove")


def downgrade() -> None:
    """
    Restore group "ALL" and reassign users.
    Note: This is a best-effort rollback - exact user mappings may not be recoverable.
    """
    conn = op.get_bind()
    
    # Create the "ALL" group
    result = conn.execute(
        sa.text("INSERT INTO camera_groups (name) VALUES ('ALL') RETURNING id")
    )
    all_group_id = result.fetchone()[0]
    
    # Assign all users with NULL group_id to the "ALL" group
    conn.execute(
        sa.text("UPDATE users SET group_id = :group_id WHERE group_id IS NULL"),
        {"group_id": all_group_id}
    )
    
    # Note: NVR, cameras, whatsapp_whitelist, group_recipients tetap NULL karena tidak tahu yang mana sebelumnya
    
    print(f"Restored group 'ALL' (id={all_group_id}) and reassigned users")