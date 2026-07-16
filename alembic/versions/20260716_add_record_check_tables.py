"""Add record check tables

Revision ID: 20260716_add_record_check_tables
Revises: a3366d14c9a4
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260716_add_record_check_tables"
down_revision = "a3366d14c9a4"
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def upgrade():
    if not table_exists("record_sources"):
        op.create_table(
            "record_sources",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("base_path", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("nvr_id", sa.String(length=36), nullable=True),
            sa.Column("stale_threshold_seconds", sa.Integer(), nullable=False),
            sa.Column("long_dead_threshold_seconds", sa.Integer(), nullable=False),
            sa.Column("scan_depth", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["nvr_id"], ["nvr.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index("ix_record_sources_name", "record_sources", ["name"])

    if not table_exists("record_check_runs"):
        op.create_table(
            "record_check_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("total_folders", sa.Integer(), nullable=False),
            sa.Column("healthy_count", sa.Integer(), nullable=False),
            sa.Column("stale_count", sa.Integer(), nullable=False),
            sa.Column("long_dead_count", sa.Integer(), nullable=False),
            sa.Column("unknown_count", sa.Integer(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["source_id"], ["record_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_record_check_runs_source_started", "record_check_runs", ["source_id", "started_at"])
        op.create_index("idx_record_check_runs_status", "record_check_runs", ["status"])

    if not table_exists("record_folder_checks"):
        op.create_table(
            "record_folder_checks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("camera_id", sa.String(length=36), nullable=True),
            sa.Column("folder_name", sa.String(length=255), nullable=False),
            sa.Column("folder_path", sa.Text(), nullable=False),
            sa.Column("last_mtime", sa.DateTime(timezone=True), nullable=True),
            sa.Column("age_seconds", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["record_check_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["record_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_record_folder_checks_run", "record_folder_checks", ["run_id"])
        op.create_index("idx_record_folder_checks_source_status", "record_folder_checks", ["source_id", "status"])
        op.create_index("idx_record_folder_checks_camera", "record_folder_checks", ["camera_id"])

    if not table_exists("record_folder_statuses"):
        op.create_table(
            "record_folder_statuses",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("camera_id", sa.String(length=36), nullable=True),
            sa.Column("folder_name", sa.String(length=255), nullable=False),
            sa.Column("folder_path", sa.Text(), nullable=False),
            sa.Column("last_mtime", sa.DateTime(timezone=True), nullable=True),
            sa.Column("age_seconds", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("alert_active", sa.Boolean(), nullable=False),
            sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_recovery_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_id"], ["record_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_id", "folder_name", name="uq_record_folder_status_source_folder"),
        )
        op.create_index("idx_record_folder_status_source_status", "record_folder_statuses", ["source_id", "status"])
        op.create_index("idx_record_folder_status_camera", "record_folder_statuses", ["camera_id"])

    if not table_exists("record_folder_mappings"):
        op.create_table(
            "record_folder_mappings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("folder_name", sa.String(length=255), nullable=False),
            sa.Column("camera_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_id"], ["record_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_id", "folder_name", name="uq_record_folder_mapping_source_folder"),
        )
        op.create_index("idx_record_folder_mapping_camera", "record_folder_mappings", ["camera_id"])

    if not table_exists("record_status_events"):
        op.create_table(
            "record_status_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_id", sa.String(length=36), nullable=False),
            sa.Column("folder_status_id", sa.String(length=36), nullable=False),
            sa.Column("camera_id", sa.String(length=36), nullable=True),
            sa.Column("folder_name", sa.String(length=255), nullable=False),
            sa.Column("previous_status", sa.String(length=20), nullable=True),
            sa.Column("new_status", sa.String(length=20), nullable=False),
            sa.Column("event_type", sa.String(length=30), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("notification_sent", sa.Boolean(), nullable=False),
            sa.Column("notification_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["folder_status_id"], ["record_folder_statuses.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["record_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_record_status_events_source_created", "record_status_events", ["source_id", "created_at"])
        op.create_index("idx_record_status_events_type", "record_status_events", ["event_type"])


def downgrade():
    for table_name, index_name in [
        ("record_status_events", "idx_record_status_events_type"),
        ("record_status_events", "idx_record_status_events_source_created"),
        ("record_folder_mappings", "idx_record_folder_mapping_camera"),
        ("record_folder_statuses", "idx_record_folder_status_camera"),
        ("record_folder_statuses", "idx_record_folder_status_source_status"),
        ("record_folder_checks", "idx_record_folder_checks_camera"),
        ("record_folder_checks", "idx_record_folder_checks_source_status"),
        ("record_folder_checks", "idx_record_folder_checks_run"),
        ("record_check_runs", "idx_record_check_runs_status"),
        ("record_check_runs", "idx_record_check_runs_source_started"),
        ("record_sources", "ix_record_sources_name"),
    ]:
        if table_exists(table_name) and index_exists(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in [
        "record_status_events",
        "record_folder_mappings",
        "record_folder_statuses",
        "record_folder_checks",
        "record_check_runs",
        "record_sources",
    ]:
        if table_exists(table_name):
            op.drop_table(table_name)
