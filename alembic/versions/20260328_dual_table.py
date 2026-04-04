"""dual_table

Revision ID: 20260328_dual_table
Revises: a0b22741533a
Create Date: 2026-03-28 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260328_dual_table'
down_revision: Union[str, None] = 'a0b22741533a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Dual-table audit log setup for 6-month archival."""
    
    # Step 1: Rename existing table to legacy
    op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")
    op.execute("ALTER SEQUENCE audit_logs_id_seq RENAME TO audit_logs_legacy_id_seq")
    
    # Drop triggers from legacy table (now can be archived)
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs_legacy")
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs_legacy")
    
    # Step 2: Create new audit_logs table (P2-001 append-only)
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
    
    # Create indexes
    op.create_index(op.f('ix_audit_logs_user'), 'audit_logs', ['user'], unique=False)
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_target'), 'audit_logs', ['target'], unique=False)
    op.create_index(op.f('ix_audit_logs_timestamp'), 'audit_logs', ['timestamp'], unique=False)
    
    # Step 3: Create append-only triggers for new table
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be deleted. This is an append-only table (P2-001). Archive period: 6 months.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        
        CREATE TRIGGER audit_log_prevent_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_delete();
    """)
    
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit logs cannot be modified. This is an append-only table (P2-001).';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        
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
    
    # Step 4: Create unified view
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
    
    # Step 5: Create archive tracking table
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
        sa.Column('checksum', sa.String(length=64), nullable=True),  # SHA-256
        sa.Column('encryption_key_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='completed'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index(op.f('ix_audit_archive_history_archived_at'), 
                    'audit_archive_history', ['archived_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema: Merge back to single table."""
    
    # Drop unified view
    op.execute("DROP VIEW IF EXISTS audit_logs_unified")
    
    # Drop archive history table
    op.drop_table('audit_archive_history')
    
    # Drop new audit_logs table with triggers
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_delete ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_log_prevent_update ON audit_logs")
    op.drop_table('audit_logs')
    
    # Rename legacy back
    op.execute("ALTER TABLE audit_logs_legacy RENAME TO audit_logs")
    op.execute("ALTER SEQUENCE audit_logs_legacy_id_seq RENAME TO audit_logs_id_seq")
    
    # Recreate triggers on original table
    op.execute("""
        CREATE TRIGGER audit_log_prevent_delete
            BEFORE DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_delete();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_prevent_update
            BEFORE UPDATE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_log_update();
    """)
