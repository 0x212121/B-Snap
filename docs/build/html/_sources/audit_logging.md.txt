# Audit Logging System

B-Snap implements a comprehensive audit logging system designed for CCTV environments where evidence integrity and compliance are critical. The system follows a **dual-table strategy** with append-only protection for current data and archival capabilities for historical records.

## Overview

### Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐
│  audit_logs     │     │   Export     │     │  Encrypted  │
│  (>6 months)    │────▶│  to JSON     │────▶│  Archive    │
│  append-only    │     │  + gzip      │     │  File       │
└─────────────────┘     └──────────────┘     └─────────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│audit_logs_legacy│◀────│    Copy      │
│  (warm storage) │     │   Verified   │
└─────────────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐
│  Truncate after │
│   verification  │
└─────────────────┘
```

### Tables

| Table | Purpose | Modifiable |
|-------|---------|------------|
| `audit_logs` | Current audit records (last 6 months) | ❌ Append-only (triggers prevent UPDATE/DELETE) |
| `audit_logs_legacy` | Historical records copied during archive; remains queryable in Archive History | Retained for archive browsing |
| `audit_archive_history` | Archive operation tracking | ✅ Records each archive job |
| `audit_logs_unified` | Database view combining both tables | ❌ Read-only view |

## P2-001: Append-Only Audit Log

### Database Triggers

PostgreSQL triggers enforce immutability at the database level:

```sql
-- Prevent DELETE
CREATE TRIGGER audit_log_prevent_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_log_delete();

-- Prevent UPDATE
CREATE TRIGGER audit_log_prevent_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_log_update();
```

Attempting to modify or delete records results in:
```
ERROR: Audit logs cannot be deleted. This is an append-only table (P2-001).
```

### Enhanced Logging Fields

Every audit log entry captures:

| Field | Type | Description |
|-------|------|-------------|
| `id` | BigInteger | Auto-increment primary key |
| `timestamp` | DateTime(TZ) | UTC timestamp of event |
| `user` | String(100) | Username who performed action |
| `action` | String(50) | Type of action (view_snapshot, create_camera, etc.) |
| `target` | String(200) | Resource affected (camera hostname, snapshot ID, etc.) |
| `ip` | String(45) | Client IP address (IPv6 compatible) |
| `user_agent` | String(500) | HTTP User-Agent header |
| `request_path` | String(500) | API endpoint accessed |
| `request_method` | String(10) | HTTP method (GET, POST, PUT, DELETE) |
| `response_status` | Integer | HTTP response code |
| `extra` | Text | Additional JSON or text data |

## Archive Strategy (6-Month Cycle)

### Why 6 Months?

- **Performance**: Keeps query times fast for recent data
- **Storage**: Reduces database size growth
- **Compliance**: Most CCTV investigations focus on recent incidents
- **Retrieval**: Historical data still accessible via unified view

### Archive Process

1. **Identify**: Logs older than 180 days (6 months)
2. **Export**: Convert to JSON format
3. **Compress**: Gzip compression (~90% size reduction)
4. **Encrypt**: AES-256-CBC encryption using Fernet
5. **Stage and Verify**: Write a temporary encrypted file and verify its checksum and contents
6. **Copy**: Insert into `audit_logs_legacy` table
7. **Publish**: Move the verified file into `archives/audit_logs/`
8. **Delete**: Remove from `audit_logs` (trigger temporarily disabled)
9. **Record**: Log operation in `audit_archive_history`

Failed archive attempts are excluded from Archive History and temporary or published files from the failed attempt are removed.

### Archive File Format

```
archives/audit_logs/
└── audit_archive_YYYYMMDD_HHMMSS_YYYYMM_YYYYMM.json.gz.enc
    │    │              │         │      │
    │    │              │         │      └── End month
    │    │              │         └───────── Start month
    │    │              └─────────────────── Timestamp
    │    └────────────────────────────────── Archive date
    └─────────────────────────────────────── Prefix
```

### Encryption

```python
from cryptography.fernet import Fernet

# Key from environment variable
key = os.environ['AUDIT_ARCHIVE_KEY']
f = Fernet(key)
encrypted = f.encrypt(compressed_data)
```

**⚠️ Important**: Store `AUDIT_ARCHIVE_KEY` securely. Loss of this key makes archives unrecoverable.

## API Endpoints

### Query Audit Logs
```
GET /audit-logs/api
Parameters:
  - page: int (default: 1)
  - per_page: int (default: 50)
  - search: str (search user, target, path)
  - action: str (filter by action type)
  - method: str (GET, POST, PUT, DELETE)
  - status_code: int (HTTP response code)
  - start_date: YYYY-MM-DD
  - end_date: YYYY-MM-DD
```

### Archive Statistics
```
GET /audit-logs/api/stats
Response:
{
  "current_table": {"records": 15000, "oldest": "...", "newest": "..."},
  "legacy_table": {"records": 450000},
  "archive_history": {"total_archives": 8},
  "eligible_for_archive": 3200
}
```

### Trigger Archive
```
POST /audit-logs/api/archive
Response:
{
  "status": "success",
  "message": "Successfully archived 3200 records",
  "file_path": "archives/audit_logs/audit_archive_...",
  "checksum": "sha256_hash..."
}
```

### Archive History
```
GET /audit-logs/api/archive/history
Parameters:
  - page: int
  - per_page: int
```

### Query Legacy Logs
```
GET /audit-logs/api/legacy
Parameters:
  - page: int
  - per_page: int
  - search: str
```

## Scheduled Jobs

### Automatic Archive

Runs automatically every 6 months:

```python
# Scheduler configuration
scheduler.add_job(
    scheduled_audit_archive,
    'cron',
    month='1,7',    # January and July
    day=1,
    hour=2,         # 2 AM
    minute=0
)
```

### Archive Check

Weekly recommendation check:

```python
check_archive_needed() -> {
    "archive_recommended": True,  # If > 10,000 records
    "archive_urgent": False,      # If > 50,000 records
    "stats": {...}
}
```

## UI Features

### Archive Warning Banner

Appears when > 1,000 records are eligible for archive:

```
┌─────────────────────────────────────────────────────────┐
│ ⚠️  Archive Recommended                                  │
│ 3,200 records older than 6 months should be archived    │
│ [Archive Now]                                           │
└─────────────────────────────────────────────────────────┘
```

### Archive History Modal

Shows:
- Current vs Legacy table record counts
- Total archive operations
- History of each archive job with:
  - Timestamp
  - Records archived
  - File size
  - Checksum (partial)
  - Status (completed/failed)

## Action Categories

| Category | Actions |
|----------|---------|
| **P0 - Evidence** | view_snapshot, download_snapshot, verify_integrity |
| **P1 - Security** | view_camera_password, view_nvr_password |
| **P2 - Compliance** | gallery_view, retention_hold_enabled, retention_hold_disabled |
| **CRUD** | create_camera, update_camera, delete_camera, purge_snapshot |
| **Auth** | login, logout |

## Best Practices

### For Administrators

1. **Monitor Archive Status**: Check `/audit-logs` page weekly
2. **Manual Archive**: Trigger before database maintenance
3. **Backup Keys**: Store `AUDIT_ARCHIVE_KEY` in password manager
4. **Verify Archives**: Periodically test archive integrity
5. **Storage Planning**: Ensure `archives/` directory has sufficient space

### For Developers

1. **Always Use Logger**: 
   ```python
   from app.utils.audit_logger import log_audit
   log_audit(db, user="admin", action="custom_action", target="resource")
   ```

2. **Include Context**:
   ```python
   log_audit(
       db, user=user, action="view_snapshot", target=snapshot_id,
       user_agent=request.headers.get("user-agent"),
       request_path=str(request.url.path),
       request_method=request.method,
       response_status=200
   )
   ```

3. **Sensitive Actions**: Always log password access:
   ```python
   log_audit(db, user=user, action="view_camera_password", 
             target=camera.hostname, extra=f"Camera ID: {camera_id}")
   ```

## Troubleshooting

### Archive Job Failed

1. Check logs: `logs/audit_archive.log`
2. Verify `AUDIT_ARCHIVE_KEY` is configured without printing its value.
3. Check disk space: `df -h archives/`
4. Review database connection

### Cannot Delete Audit Log (Expected Behavior)

```
ERROR: Audit logs cannot be deleted. This is an append-only table (P2-001).
```

✅ This is correct behavior. Use archive process for old data.

### Archive File Corrupted

1. Verify checksum matches `audit_archive_history` table
2. Check file permissions
3. Restore from backup if necessary
4. Decrypt test:
   ```python
   from app.jobs.audit_archive import AuditArchiveService
   service = AuditArchiveService(db)
   # Verify integrity
   ```

## Migration Notes

### From Single Table to Dual Table

The migration (`20260328_dual_table_audit_logs.py`) handles:

1. Renames existing `audit_logs` → `audit_logs_legacy`
2. Creates new `audit_logs` with append-only triggers
3. Creates unified view
4. Creates archive history table

**No data loss** - existing data remains accessible via:
- Direct query: `SELECT * FROM audit_logs_legacy`
- Unified view: `SELECT * FROM audit_logs_unified`
- API: `GET /audit-logs/api/legacy`

## Compliance

### ISO 27001
- **A.12.4.1** - Event logging (✅ All access logged)
- **A.12.4.2** - Protection of log information (✅ Append-only, encrypted archives)
- **A.12.4.3** - Administrator and operator logs (✅ All admin actions logged)

### SOC 2 Type II
- **CC6.1** - Logical access security (✅ Password access audited)
- **CC7.2** - System monitoring (✅ Comprehensive audit trail)
- **CC7.3** - System recovery (✅ Archived logs retrievable)

### CCTV Evidence Standards
- **Chain of Custody**: Every access logged with user, timestamp, IP
- **Tamper Evidence**: SHA-256 checksums on archives
- **Retention**: Configurable retention periods with legal hold support

### Archive key persistence in containers

Set a valid `AUDIT_ARCHIVE_KEY` before running archival. Web and scheduler must
use the same stable Fernet key. Missing/invalid keys reject the operation before
publishing archives or deleting source records; temporary keys are not generated.
New archive metadata stores a SHA-256 fingerprint instead of a key prefix.
Existing encrypted archives continue to need their original key. The Compose
services share `/app/archives/audit_logs` through a persistent host bind mount.
