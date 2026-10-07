"""Create the historical base tables missing from the original migration chain.

Frozen DDL: do not import live models here. Later revisions add their own columns,
tables, audit triggers and views. Existing versioned databases skip this ancestor.
"""

from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import ENUM

from alembic import op

revision = "20261007_baseline"
down_revision = None
branch_labels = None
depends_on = None

# PostgreSQL schema before the original ed2356cb250c revision.
TABLES = (
    (
        "audit_logs",
        """CREATE TABLE audit_logs (
    id BIGSERIAL NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    "user" VARCHAR(100) NOT NULL,
    action VARCHAR(50) NOT NULL,
    target VARCHAR(200),
    ip VARCHAR(45),
    extra TEXT,
    PRIMARY KEY (id)
)""",
        [
            "CREATE INDEX ix_audit_logs_action ON audit_logs (action)",
            "CREATE INDEX ix_audit_logs_target ON audit_logs (target)",
            "CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp)",
            'CREATE INDEX ix_audit_logs_user ON audit_logs ("user")',
        ],
    ),
    (
        "camera_groups",
        """CREATE TABLE camera_groups (
    id SERIAL NOT NULL,
    name VARCHAR NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (name)
)""",
        [],
    ),
    (
        "configurations",
        """CREATE TABLE configurations (
    key VARCHAR NOT NULL,
    value VARCHAR,
    PRIMARY KEY (key)
)""",
        [],
    ),
    (
        "health_check_status",
        """CREATE TABLE health_check_status (
    id SERIAL NOT NULL,
    is_running BOOLEAN,
    start_time TIMESTAMP WITH TIME ZONE,
    total_cameras INTEGER,
    completed_cameras INTEGER,
    PRIMARY KEY (id)
)""",
        [],
    ),
    (
        "notification_queue",
        """CREATE TABLE notification_queue (
    id SERIAL NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
)""",
        [],
    ),
    (
        "task_timings",
        """CREATE TABLE task_timings (
    id SERIAL NOT NULL,
    task_name VARCHAR NOT NULL,
    device_id VARCHAR,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_ms INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    PRIMARY KEY (id)
)""",
        ["CREATE INDEX ix_task_timings_id ON task_timings (id)"],
    ),
    (
        "cameras",
        """CREATE TABLE cameras (
    id VARCHAR(36) NOT NULL,
    hostname VARCHAR NOT NULL,
    previous_name VARCHAR,
    ip VARCHAR,
    port INTEGER,
    username VARCHAR,
    password TEXT,
    latitude FLOAT,
    longitude FLOAT,
    previous_latitude FLOAT,
    previous_longitude FLOAT,
    asset_no VARCHAR,
    location VARCHAR,
    status VARCHAR,
    group_id INTEGER,
    PRIMARY KEY (id),
    UNIQUE (id),
    FOREIGN KEY(group_id) REFERENCES camera_groups (id)
)""",
        ["CREATE UNIQUE INDEX ix_cameras_hostname ON cameras (hostname)"],
    ),
    (
        "nvr",
        """CREATE TABLE nvr (
    id VARCHAR(36) NOT NULL,
    hostname VARCHAR(100) NOT NULL,
    ip VARCHAR(45) NOT NULL,
    username VARCHAR(100),
    password TEXT,
    location VARCHAR(100),
    status VARCHAR(15),
    asset_no VARCHAR,
    latitude FLOAT,
    longitude FLOAT,
    group_id INTEGER,
    PRIMARY KEY (id),
    FOREIGN KEY(group_id) REFERENCES camera_groups (id)
)""",
        ["CREATE UNIQUE INDEX ix_nvr_hostname ON nvr (hostname)"],
    ),
    (
        "users",
        """CREATE TABLE users (
    id SERIAL NOT NULL,
    username VARCHAR NOT NULL,
    password VARCHAR NOT NULL,
    role VARCHAR NOT NULL,
    last_login TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE,
    token VARCHAR,
    token_expires_at TIMESTAMP WITH TIME ZONE,
    otp_secret VARCHAR,
    is_2fa_enabled BOOLEAN NOT NULL,
    web_tokens JSONB,
    api_tokens JSONB,
    group_id INTEGER,
    PRIMARY KEY (id),
    UNIQUE (username),
    FOREIGN KEY(group_id) REFERENCES camera_groups (id)
)""",
        [],
    ),
    (
        "whatsapp_whitelist",
        """CREATE TABLE whatsapp_whitelist (
    id SERIAL NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    role role_enum NOT NULL,
    added_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id)
)""",
        [
            "CREATE INDEX ix_whatsapp_whitelist_id ON whatsapp_whitelist (id)",
            "CREATE UNIQUE INDEX ix_whatsapp_whitelist_phone_number ON whatsapp_whitelist (phone_number)",
        ],
    ),
    (
        "camera_daily_stats",
        """CREATE TABLE camera_daily_stats (
    id SERIAL NOT NULL,
    camera_id VARCHAR(36),
    camera_name VARCHAR(60) NOT NULL,
    date DATE NOT NULL,
    uptime_percentage FLOAT,
    snapshot_count INTEGER,
    total_uptime_seconds INTEGER,
    total_downtime_seconds INTEGER,
    checked TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE CASCADE
)""",
        [],
    ),
    (
        "camera_health",
        """CREATE TABLE camera_health (
    id VARCHAR(36) NOT NULL,
    status VARCHAR NOT NULL,
    status_changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    latency INTEGER,
    checked TIMESTAMP WITH TIME ZONE NOT NULL,
    last_online TIMESTAMP WITH TIME ZONE NOT NULL,
    type VARCHAR NOT NULL,
    camera_id VARCHAR(36),
    nvr_id VARCHAR(36),
    PRIMARY KEY (id),
    FOREIGN KEY(nvr_id) REFERENCES nvr (id) ON DELETE CASCADE,
    FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE CASCADE
)""",
        [],
    ),
    (
        "camera_status_change_log",
        """CREATE TABLE camera_status_change_log (
    id VARCHAR(36) NOT NULL,
    camera_id VARCHAR(36),
    previous_status VARCHAR NOT NULL,
    new_status VARCHAR NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_since_last_change INTEGER,
    PRIMARY KEY (id),
    UNIQUE (id),
    FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE CASCADE
)""",
        [],
    ),
    (
        "snapshot_logs",
        """CREATE TABLE snapshot_logs (
    id VARCHAR(36) NOT NULL,
    camera_name VARCHAR(50),
    timestamp TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (id),
    CONSTRAINT snapshot_logs_camera_name_fkey FOREIGN KEY(camera_name) REFERENCES cameras (hostname)
)""",
        ["CREATE INDEX ix_snapshot_logs_id ON snapshot_logs (id)"],
    ),
    (
        "snapshots",
        """CREATE TABLE snapshots (
    id VARCHAR(36) NOT NULL,
    camera_id VARCHAR(36),
    camera_name VARCHAR NOT NULL,
    camera_ip VARCHAR NOT NULL,
    camera_port INTEGER,
    camera_location VARCHAR,
    camera_group VARCHAR,
    timestamp TIMESTAMP WITH TIME ZONE,
    file_path VARCHAR NOT NULL,
    file_size INTEGER,
    resolution VARCHAR,
    PRIMARY KEY (id),
    FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE SET NULL
)""",
        [],
    ),
    (
        "videos",
        """CREATE TABLE videos (
    id VARCHAR(36) NOT NULL,
    camera_id VARCHAR(36),
    camera_name VARCHAR NOT NULL,
    camera_ip VARCHAR,
    camera_group VARCHAR,
    timestamp TIMESTAMP WITH TIME ZONE,
    file_path VARCHAR NOT NULL,
    file_size INTEGER,
    duration INTEGER,
    resolution VARCHAR(12),
    PRIMARY KEY (id),
    UNIQUE (file_path),
    FOREIGN KEY(camera_id) REFERENCES cameras (id) ON DELETE SET NULL
)""",
        [],
    ),
)


def upgrade() -> None:
    """Create only missing base tables; preserve legacy unversioned tables."""
    bind = op.get_bind()
    ENUM("admin", "user", name="role_enum").create(bind, checkfirst=True)
    existing = set(inspect(bind).get_table_names())
    for name, ddl, indexes in TABLES:
        if name not in existing:
            op.execute(ddl)
            for index in indexes:
                op.execute(index)


def downgrade() -> None:
    """Keep historical base data when returning to an unversioned database."""
    # The old application owned these tables outside Alembic. Never drop them.
