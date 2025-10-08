"""add recipients table for email logs

Revision ID: 300b57036bb7
Revises: 69cfe8bc0f54
Create Date: 2025-09-26 23:22:24.343219

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '300b57036bb7'
down_revision: Union[str, None] = 'fe1c28ede362'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "camera_email_notification_logs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("camera_id", sa.String, sa.ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("camera_name", sa.String, nullable=True),
        sa.Column("incident_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("success", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("error_message", sa.String, nullable=True),
    )
    op.create_unique_constraint(
        "uq_camera_incident_once",
        "camera_email_notification_logs",
        ["camera_id", "incident_started_at"],
    )

    op.create_table(
        "camera_email_notification_log_recipients",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("log_id", sa.Integer, sa.ForeignKey("camera_email_notification_logs.id", ondelete="CASCADE")),
        sa.Column("recipient_email", sa.String, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("camera_email_notification_log_recipients")
    op.drop_constraint("uq_camera_incident_once", "camera_email_notification_logs", type_="unique")
    op.drop_table("camera_email_notification_logs")