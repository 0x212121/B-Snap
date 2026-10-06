"""Add managed SMB/NFS record sources without changing existing local sources."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261006_record_mounts"
down_revision = "20260930_wa_provider_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "record_sources",
        sa.Column("connection_type", sa.String(10), nullable=False, server_default="local"),
    )
    op.add_column("record_sources", sa.Column("server", sa.String(45), nullable=True))
    op.add_column("record_sources", sa.Column("remote_path", sa.Text(), nullable=True))
    op.add_column("record_sources", sa.Column("smb_username", sa.String(255), nullable=True))
    op.add_column("record_sources", sa.Column("smb_domain", sa.String(255), nullable=True))
    op.add_column("record_sources", sa.Column("smb_password_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "record_sources",
        sa.Column("nfs_security", sa.String(10), nullable=False, server_default="krb5p"),
    )


def downgrade() -> None:
    for name in (
        "nfs_security",
        "smb_password_encrypted",
        "smb_domain",
        "smb_username",
        "remote_path",
        "server",
        "connection_type",
    ):
        op.drop_column("record_sources", name)
