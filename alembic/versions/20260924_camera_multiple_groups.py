"""Add many-to-many camera group assignments.

Revision ID: 20260924_camera_multiple_groups
Revises: 20260716_add_record_check_tables
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260924_camera_multiple_groups"
down_revision: Union[str, None] = "20260716_add_record_check_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "camera_camera_groups",
        sa.Column("camera_id", sa.String(length=36), nullable=False),
        sa.Column("camera_group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["camera_group_id"], ["camera_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("camera_id", "camera_group_id"),
    )
    # Preserve every current assignment. Keep cameras.group_id as a compatibility
    # field for older API consumers during the transition.
    op.execute(sa.text(
        "INSERT INTO camera_camera_groups (camera_id, camera_group_id) "
        "SELECT id, group_id FROM cameras WHERE group_id IS NOT NULL"
    ))
    op.create_index(
        "ix_camera_camera_groups_group_camera",
        "camera_camera_groups",
        ["camera_group_id", "camera_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_camera_camera_groups_group_camera", table_name="camera_camera_groups")
    op.drop_table("camera_camera_groups")
