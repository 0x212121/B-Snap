"""remove_all_group

Revision ID: 20260330_remove_all_group
Revises: 20260330_alert_cooldown
Create Date: 2026-03-30 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260330_remove_all_group'
down_revision: Union[str, None] = '20260330_alert_cooldown'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Remove group "ALL" and set users with that group to have no group (NULL).
    Users with NULL group_id now have access to all cameras.
    """
    # Get database connection
    conn = op.get_bind()
    
    # Find the "ALL" group
    result = conn.execute(sa.text("SELECT id FROM camera_groups WHERE name = 'ALL'"))
    all_group = result.fetchone()
    
    if all_group:
        all_group_id = all_group[0]
        
        # Update users who had the "ALL" group to have NULL group_id
        # This gives them access to all cameras (new permission model)
        conn.execute(
            sa.text("UPDATE users SET group_id = NULL WHERE group_id = :group_id"),
            {"group_id": all_group_id}
        )
        
        # Delete the "ALL" group
        conn.execute(
            sa.text("DELETE FROM camera_groups WHERE id = :group_id"),
            {"group_id": all_group_id}
        )
        
        print(f"Removed group 'ALL' (id={all_group_id}) and updated users to have NULL group_id")
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
    
    print(f"Restored group 'ALL' (id={all_group_id}) and reassigned users")
