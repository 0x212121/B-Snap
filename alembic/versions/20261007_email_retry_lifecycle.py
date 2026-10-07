"""Preserve notification incidents and release completed retry tasks.

Revision ID: 20261007_email_retry
Revises: 20261007_api_daily_stats
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261007_email_retry"
down_revision = "20261007_api_daily_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Backfill old tasks and restrict uniqueness to pending notifications."""
    op.add_column("email_retry_queue", sa.Column("incident_time", sa.DateTime(timezone=True)))
    op.add_column("email_retry_queue", sa.Column("offline_duration_seconds", sa.Integer()))
    op.add_column("email_retry_queue", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "email_retry_queue",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "camera_email_notification_logs",
        sa.Column("retry_exhausted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Recover an initial failed notification when its log immediately preceded enqueue.
    # Older tasks without a matching log retain created_at as the best known timestamp.
    op.execute("""
        UPDATE email_retry_queue AS q
        SET incident_time = COALESCE((
            SELECT l.incident_started_at
            FROM camera_email_notification_logs AS l
            WHERE l.camera_id = q.camera_id
              AND l.reason = CASE WHEN q.type = 'tamper' THEN q.reason ELSE q.type END
              AND l.sent_at <= q.created_at
              AND l.sent_at >= q.created_at - INTERVAL '5 minutes'
            ORDER BY l.sent_at DESC, l.id DESC LIMIT 1
        ), q.created_at),
        status = CASE WHEN sent THEN 'sent'
                      WHEN attempts >= max_attempts THEN 'exhausted'
                      ELSE 'pending' END,
        completed_at = CASE WHEN sent OR attempts >= max_attempts
                            THEN COALESCE(last_attempt, created_at) ELSE NULL END
    """)
    op.execute("""
        UPDATE email_retry_queue
        SET offline_duration_seconds = GREATEST(
            0, EXTRACT(EPOCH FROM (created_at - incident_time))::integer
        ) WHERE type = 'offline'
    """)
    op.execute("""
        UPDATE camera_email_notification_logs AS l SET retry_exhausted = true
        FROM email_retry_queue AS q
        WHERE q.status = 'exhausted' AND l.success = false
          AND l.camera_id = q.camera_id AND l.incident_started_at = q.incident_time
    """)
    op.alter_column("email_retry_queue", "incident_time", nullable=False)
    op.create_check_constraint(
        "ck_email_retry_status",
        "email_retry_queue",
        "status IN ('pending', 'sent', 'exhausted', 'cancelled')",
    )
    op.drop_index("ux_email_retry_active", table_name="email_retry_queue")
    op.create_index(
        "ux_email_retry_active",
        "email_retry_queue",
        ["camera_id", "type"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Restore legacy uniqueness; fail if history cannot fit the old constraint."""
    op.drop_index("ux_email_retry_active", table_name="email_retry_queue")
    op.create_index(
        "ux_email_retry_active",
        "email_retry_queue",
        ["camera_id", "type"],
        unique=True,
        postgresql_where=sa.text("sent = false"),
    )
    op.drop_constraint("ck_email_retry_status", "email_retry_queue", type_="check")
    op.drop_column("camera_email_notification_logs", "retry_exhausted")
    for name in ("status", "completed_at", "offline_duration_seconds", "incident_time"):
        op.drop_column("email_retry_queue", name)
