"""dual_table

Revision ID: 20260328_dual_table
Revises: a0b22741533a
Create Date: 2026-03-28 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision: str = '20260328_dual_table'
down_revision: Union[str, None] = 'a0b22741533a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if table exists in database."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if index exists on table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes(table_name)
    return any(idx['name'] == index_name for idx in indexes)


def view_exists(view_name: str) -> bool:
    """Check if view exists in database."""
    bind = op.get_bind()
    result = bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_views WHERE viewname = :name)"
    ), {"name": view_name}).scalar()
    return result


def sequence_exists(seq_name: str) -> bool:
    """Check if sequence exists."""
    bind = op.get_bind()
    result = bind.execute(text(
        "SELECT EXISTS (SELECT 1 FROM pg_sequences WHERE sequencename = :name)"
    ), {"name": seq_name}).scalar()
    return result


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(col['name'] == column_name for col in columns)


def upgrade() -> None:
    """Upgrade schema: Dual-table audit log setup for 6-month archival."""
    
    # CLEANUP: Hapus artifact dari failed migration sebelumnya jika ada
    
    # 1. Drop view jika exist
    if view_exists('audit_logs_unified'):
        op.execute("DROP VIEW IF EXISTS audit_logs_unified")
    
    # 2. Drop archive history table jika exist
    if table_exists('audit_archive_history'):
        if index_exists('audit_archive_history', 'ix_audit_archive_history_archived_at'):
            op.drop_index('ix_audit_archive_history_archived_at', table_name='audit_archive_history')
        op.drop_table('audit_archive_history')
    
    # 3. Drop triggers dari audit_logs_legacy jika exist (dari failed sebelumnya)
    if table_exists('audit_logs_legacy'):
        op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs_legacy")
        op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs_legacy")
        # Drop indexes dari legacy untuk avoid name conflict
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_user")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_action")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_target")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_timestamp")
        op.execute("DROP TABLE IF EXISTS audit_logs_legacy CASCADE")
    
    # 4. Drop sequence legacy jika exist
    if sequence_exists('audit_logs_legacy_id_seq'):
        op.execute("DROP SEQUENCE IF EXISTS audit_logs_legacy_id_seq")
    
    # 5. Pastikan audit_logs exist sebelum rename
    if not table_exists('audit_logs'):
        raise Exception("audit_logs table does not exist. Cannot proceed with dual-table migration.")
    
    # 6. Pastikan audit_logs punya kolom lengkap (dari a0b22741533a) sebelum rename
    required_cols = ['user_agent', 'request_path', 'request_method', 'response_status']
    missing_cols = [col for col in required_cols if not column_exists('audit_logs', col)]
    if missing_cols:
        raise Exception(f"audit_logs missing columns {missing_cols}. Run a0b22741533a first.")
    
    # Step 1: Rename existing table to legacy
    op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")
    
    # Step 2: Rename sequence
    if sequence_exists('audit_logs_id_seq'):
        op.execute("ALTER SEQUENCE audit_logs_id_seq RENAME TO audit_logs_legacy_id_seq")
    
    # Step 3: Drop indexes dari legacy table untuk avoid name conflict dengan tabel baru
    # Index di legacy table tidak critical karena data jarang di-query langsung
    # (biasanya di-query via unified view atau di-archive ke cold storage)
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_user")
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_action")
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_target")
    op.execute("DROP INDEX IF EXISTS ix_audit_logs_timestamp")
    
    # Step 4: Create new audit_logs table (P2-001 append-only)
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, 
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('user', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('target', sa.String(length=200), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.Column('extra', sa.Text(), nullable=True),
        sa.Column('user_agent', sa.String(length=500), nullable=True),
        sa.Column('request_path', sa.String(length=500), nullable=True),
        sa.Column('request_method', sa.String(length=10), nullable=True),
        sa.Column('response_status', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Step 5: Create indexes di tabel baru (sekarang aman karena yang lama sudah di-drop)
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_user ON audit_logs (\"user\")")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs (action)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_target ON audit_logs (target)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp ON audit_logs (timestamp)")
    
    # Step 6: Create append-only triggers untuk new table
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be deleted. This is an append-only table (P2-001). Archive period: 6 months.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be modified. This is an append-only table (P2-001).';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Drop trigger dulu jika exist (idempotent)
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs")
    op.execute("""
        CREATE TRIGGER audit_log_prevent_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_delete();
    """)
    
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs")
    op.execute("""
        CREATE TRIGGER audit_log_prevent_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_update();
    """)
    
    # Add comments
    op.execute("""
        COMMENT ON TABLE audit_logs IS 
        'P2-001: Append-only audit log (current). Archive to legacy every 6 months.'
    """)
    op.execute("""
        COMMENT ON TABLE audit_logs_legacy IS 
        'Historical audit logs (>6 months). Can be archived to cold storage.'
    """)
    
    # Step 7: Create unified view
    op.execute("""
        CREATE OR REPLACE VIEW audit_logs_unified AS
        SELECT 
            id,
            timestamp,
            "user",
            action,
            target,
            ip,
            extra,
            user_agent,
            request_path,
            request_method,
            response_status,
            'current' as source
        FROM audit_logs
        UNION ALL
        SELECT 
            id,
            timestamp,
            "user",
            action,
            target,
            ip,
            extra,
            user_agent,
            request_path,
            request_method,
            response_status,
            'legacy' as source
        FROM audit_logs_legacy
        ORDER BY timestamp DESC;
    """)
    
    # Step 8: Create archive tracking table
    if not table_exists('audit_archive_history'):
        op.create_table(
            'audit_archive_history',
            sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('archived_at', sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('archived_by', sa.String(length=100), nullable=False),
            sa.Column('archive_period_start', sa.DateTime(timezone=True), nullable=False),
            sa.Column('archive_period_end', sa.DateTime(timezone=True), nullable=False),
            sa.Column('records_archived', sa.BigInteger(), nullable=False),
            sa.Column('file_path', sa.String(length=500), nullable=False),
            sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
            sa.Column('checksum', sa.String(length=64), nullable=True),
            sa.Column('encryption_key_id', sa.String(length=100), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='completed'),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_archive_history_archived_at ON audit_archive_history (archived_at)")


def downgrade() -> None:
    """Downgrade schema: Merge back to single table."""
    
    # Drop unified view
    if view_exists('audit_logs_unified'):
        op.execute("DROP VIEW IF EXISTS audit_logs_unified")
    
    # Drop archive history table
    if table_exists('audit_archive_history'):
        if index_exists('audit_archive_history', 'ix_audit_archive_history_archived_at'):
            op.drop_index('ix_audit_archive_history_archived_at', table_name='audit_archive_history')
        op.drop_table('audit_archive_history')
    
    # Drop new audit_logs table with triggers
    if table_exists('audit_logs'):
        op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs")
        op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_delete() CASCADE")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_update() CASCADE")
        
        # Drop indexes
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_timestamp")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_target")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_action")
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_user")
        
        op.drop_table('audit_logs')
    
    # Rename legacy back
    if table_exists('audit_logs_legacy'):
        op.execute("ALTER TABLE audit_logs_legacy RENAME TO audit_logs")
        
        if sequence_exists('audit_logs_legacy_id_seq'):
            op.execute("ALTER SEQUENCE audit_logs_legacy_id_seq RENAME TO audit_logs_id_seq")
        
        # Recreate indexes di tabel original (legacy menjadi current lagi)
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_user ON audit_logs (\"user\")")
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs (action)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_target ON audit_logs (target)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_timestamp ON audit_logs (timestamp)")
        
        # Recreate triggers
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_audit_log_delete()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'Audit logs cannot be deleted. This is an append-only table.';
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
        """)
        
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_audit_log_update()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'Audit logs cannot be modified. This is an append-only table.';
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
        """)
        
        op.execute("""
            DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs;
            CREATE TRIGGER audit_log_prevent_delete
                BEFORE DELETE ON audit_logs
                FOR EACH ROW
                EXECUTE FUNCTION prevent_audit_log_delete();
        """)
        
        op.execute("""
            DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs;
            CREATE TRIGGER audit_log_prevent_update
                BEFORE UPDATE ON audit_logs
                FOR EACH ROW
                EXECUTE FUNCTION prevent_audit_log_update();
        """)