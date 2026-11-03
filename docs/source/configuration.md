# Configuration Guide

## Branding

Administrators can open **Branding** in the administration menu to set the
application name, login tagline, logo and primary color. Upload a PNG or ICO directly
on this page (up to 512 KB), or select an available application logo uploaded under
**Configuration → Appearance → Application Logo**. The live preview shows the draft
navigation and sign-in identity in light or dark appearance. **Cancel changes**
restores the saved settings; **Use defaults** prepares a default draft. Both draft
changes and new uploads take effect only after **Save branding** succeeds.
The logo appears in the main navigation and authentication pages,
and is used as the favicon when a custom logo is selected. The
name appears in the login title and titles of pages using the main template.
The primary color palette controls shared primary buttons and dialogs; individual
page-specific colors may remain unchanged.
Missing or invalid configuration falls back to the B-Snap defaults.

These settings are stored in the `configurations` table. Preserve the uploaded
logo file alongside database backups. New uploads are stored in `static/branding`,
which Docker Compose persists in its `branding_data` volume. Existing logos selected
under `static/icons` remain legacy assets; upload them again and select the new
entry to use persistent storage. Application uploads do not overwrite legacy map
or report assets under `static/icons`.
Direct Branding uploads use content-addressed filenames; the previous active image
is preserved if saving fails. Existing assets are retained when changing the logo
or choosing the default. Back up the whole branding directory/volume with the database.
The Insights executive PDF and health PDF use the configured name and logo; health
CSV headers and GoWA outage email alerts use the configured name. This initial
feature does not change all page-specific colors, other email and reports,
integration URLs or authentication settings.

### Map logo

**Configuration → Appearance → Map Logo** manages the image in the map heading
independently of application branding. New map uploads use `static/branding/map-logo.png`
or `map-logo.ico`, selected through the `map_logo` configuration key, and share the
persistent `branding_data` volume. Changing the application name or logo cannot
change this map image. The application logo still appears in the common navigation.

Existing `static/icons/logo.png` or `logo.ico` remains a map-only fallback until a
map logo is uploaded. A missing map image is omitted rather than replaced by the
application logo. The historical `app_logo` upload API field remains an alias for
map uploads; new forms use `map_logo` and `application_logo` separately. These
changes do not rewrite existing files or require a schema migration.

## Overview

B-Snap uses environment variables for configuration, allowing flexible deployment across different environments (development, testing, production).

**Total Environment Variables**: 40+  
**Configuration File**: `.env` (copy from `.env.example`)  
**Documentation**: Available at `/docs/environment` endpoint

---

## Quick Start

### 1. Create Configuration File

```text
cp .env.example .env
```

### 2. Generate Secure Keys

```text
# Generate SECRET_KEY (for sessions)
export SECRET_KEY=$(openssl rand -hex 32)

# Generate ENCRYPTION_KEY (for camera passwords)
export ENCRYPTION_KEY=$(openssl rand -hex 32)
```

### 3. Edit Configuration

```text
nano .env
```

---

## Core Settings (Required)

### SECRET_KEY
- **Type**: String
- **Required**: Yes
- **Description**: Secret key for session management, CSRF protection, and signed tokens
- **Security**: CRITICAL - Change in production!
- **Generation**: `openssl rand -hex 32`
- **Used In**: `app/main.py`, `app/routes/auth.py`

### ENCRYPTION_KEY
- **Type**: String (32-byte hex)
- **Required**: Yes
- **Description**: AES-256-GCM encryption key for sensitive data (camera passwords)
- **Security**: CRITICAL - Backup required! Loss = loss of camera password access
- **Generation**: `openssl rand -hex 32`
- **Used In**: `app/utils/encryption.py`

### DATABASE_URL
- **Type**: String (URL)
- **Required**: Yes
- **Default**: `postgresql+psycopg2://bsnap_user:bsnap_pass@localhost:5432/bsnap_db`
- **Description**: PostgreSQL database connection URL
- **Format**: `postgresql+psycopg2://user:password@host:port/database`
- **Used In**: `app/db/database.py`

### ENVIRONMENT
- **Type**: String
- **Required**: Yes
- **Default**: `development`
- **Options**: `development`, `testing`, `production`
- **Description**: Application environment mode
- **Impact**: Affects cookie security, debug mode, error handling
- **Used In**: `app/main.py`, `app/routes/auth.py`

---

## Session & Authentication

### SESSION_MAX_AGE_SECONDS
- **Type**: Integer (seconds)
- **Default**: `86400` (24 hours)
- **Description**: Session token expiration time
- **Security**: MED-001 - Aligned with token expiration
- **Recommendation**: Keep at 24 hours. Use Remember Me for 24/7 monitoring
- **Used In**: `app/routes/auth.py`, `app/middleware/auth_and_setup.py`

### MAX_WEB_SESSIONS
- **Type**: Integer
- **Default**: `1`
- **Description**: Maximum concurrent web sessions per user
- **Used In**: `app/routes/auth.py`

---

## Security Settings

### TRUSTED_HOSTS
- **Type**: String (comma-separated)
- **Default**: `*`
- **Description**: Trusted hosts for CORS
- **Security**: Restrict in production!
- **Example**: `b-snap.company.com,192.168.1.100`
- **Used In**: `app/main.py`

### DEBUG
- **Type**: Boolean
- **Default**: `false`
- **Description**: Debug mode flag
- **Security**: NEVER enable in production!
- **Impact**: Enables detailed error messages and auto-reload
- **Used In**: `app/main.py`

---

## Email Configuration (SMTP)

### SMTP_HOST
- **Type**: String
- **Default**: `smtp.gmail.com`
- **Description**: SMTP server hostname
- **Used In**: `app/utils/smtp_config.py`

### SMTP_PORT
- **Type**: Integer
- **Default**: `587`
- **Description**: SMTP server port
- **Used In**: `app/utils/smtp_config.py`

### SMTP_USER / SMTP_PASSWORD
- **Type**: String
- **Description**: SMTP authentication credentials
- **Security**: Keep secure!
- **Used In**: `app/utils/smtp_config.py`

### EMAIL_FROM / EMAIL_CC
- **Type**: String
- **Description**: Default sender and CC email addresses
- **Used In**: `app/utils/smtp_config.py`

### Email retry resource limits

These are database settings, editable in App Config under job settings. They are
read at the start of each retry run. Initialization seeds missing values only.

| Key | Default | Accepted range | Purpose |
| --- | --- | --- | --- |
| `email_retry_max_pending_hours` | `24` | 1?720 | Maximum pending age in hours, measured from original queue creation. |
| `email_retry_batch_size` | `50` | 1–500 | Maximum due tasks loaded in one run. |
| `email_retry_max_run_seconds` | `120` | 1–3600 | Cooperative run budget, checked before each task. |
| `email_retry_smtp_timeout_seconds` | `30` | 1–30 | Socket timeout, capped by the remaining run budget. |

Pending notifications reaching the configured age limit are cancelled with
`cancellation_reason=pending_expired`, including tasks whose next retry is not yet due.
Expiry is checked again before processing each selected task. Changing the limit applies
on the next retry run; retries do not reset queue creation time or incident time.
Job Management reports an expired count, and the existing cleanup retains cancelled
entries for seven days after completion. Apply `alembic upgrade head` before restarting
the web application and scheduler worker for this setting.

Tasks left by a batch or time limit keep their original incident, attempt count,
and retry timestamp for the next execution. An in-progress delivery can finish
after the run budget; this is not a hard process deadline. SMTP configuration is
cached only during one run, so subsequent runs pick up saved changes. Successful
delivery/failure log updates are committed before the worker moves to another task.

Job history shows actual sends, already-delivered incidents, SMTP failures,
policy/configuration deferrals and their reasons, exhausted/cancelled tasks,
processing errors, due backlog, oldest pending age, and duration. Status is `fail`
when errors occur without a completed delivery, `partial` when completed deliveries
coexist with errors or unfinished work, and `deferred` for runs containing only
deferrals or unfinished work. An empty run is `success`. `exhausted` can overlap
with `smtp_failed`, and a successful batch may still have future pending retries.

---

## Storage & Paths

### SNAPSHOT_PATH
- **Type**: String (path)
- **Default**: `./static/snapshots`
- **Description**: Directory for snapshot storage

### VIDEO_PATH
- **Type**: String (path)
- **Default**: `./static/videos`
- **Description**: Directory for video storage. Browser playback must use authenticated `/api/videos/secure/{video_id}` or `/api/videos/file/{file_path}`, not direct `/static/videos/*`.

### ffmpeg / ffprobe
- **Type**: Executable dependency
- **Description**: Video recording, thumbnail generation, and metadata extraction use FFmpeg tooling
- **Fallback**: If `ffmpeg` is not on PATH, B-Snap uses project-local `ffmpeg.exe` when present. `ffprobe` is preferred for metadata, but B-Snap falls back to parsing `ffmpeg` output if `ffprobe` is unavailable.

### LOG_DIR
- **Type**: String (path)
- **Default**: `./logs`
- **Description**: Directory for log files
- **Used In**: `app/core/logging_config.py`

### ALEMBIC_INI_PATH
- **Type**: String (path)
- **Default**: `./alembic.ini`
- **Description**: Path to Alembic migration configuration
- **Used In**: `app/main.py`

---

## Retention Policy (MED-003)

Configure captured media under **Configuration > Retention**. These are database
configuration keys read by `get_config()`, rather than uppercase environment settings.

| Key | Default | Meaning |
| --- | --- | --- |
| `retention_snapshot_mode` | `both` | Snapshot policy: `age`, `count`, or `both` |
| `retention_snapshot_days` | `30` | Maximum snapshot age in days (1-3650) |
| `max_screenshot_per_camera` | `20` | Maximum active snapshots per camera (1-1000000) |
| `retention_video_mode` | `age` | Video policy: `age`, `count`, or `both` |
| `retention_video_days` | `7` | Maximum video age in days (1-3650) |
| `max_video_per_camera` | `20` | Maximum active videos per camera (1-1000000) |

`age` ignores the count limit; `count` ignores the age limit. `both` removes media
exceeding either limit, processing age first and then keeping the newest remaining
items within each camera's count limit. Age uses the capture timestamp in UTC.
Deleted items are excluded. Media whose camera was deleted (`camera_id` is NULL)
share one count bucket. Equal capture timestamps are ordered by ID.

Retention holds always preserve items. Held items count toward a camera's limit,
so the active count may remain above the limit if held items alone exceed it.
All retention actions are soft deletes: physical files remain until an administrator
purges them. Changing a policy does not restore previously deleted media.

Settings are read on each run without restarting the worker. The **Retention Policy**
job applies both policies; its default schedule is daily at 03:00 in the configured
timezone and can be changed in **Jobs** (`retention_policy_cron`). The scheduler
worker must be running. Snapshot capture additionally applies its count policy
immediately when the mode is `count` or `both`; video count limits run through the
scheduled job. Existing saved settings are preserved when new keys are seeded.

Implementation: `app/utils/media_retention.py`, `app/jobs/scheduler.py`, and
`app/utils/snapshot_utils.py`.

### retention_record_check_days
- **Type**: Integer
- **Default**: `90`
- **Description**: Hard-deletes old record-check run, folder-check, and status-event history older than this many days
- **Note**: Record sources, folder mappings, and current folder statuses are preserved
- **Used In**: `app/jobs/scheduler.py`, `app/utils/record_check.py`

### retention_api_logs_days

- **Default**: `90`; configurable through **Log Retention → API Logs (days)**.
- **Cleanup**: `cleanup_api_logs` follows its own cron override or the shared
  cleanup schedule. Before deleting detailed logs, it archives expired complete
  local days into `api_daily_stats` in the same transaction. The incomplete
  cutoff day stays in detailed logs until the next eligible cleanup.
- **Analytics**: daily charts, totals, comparisons, and reports combine retained
  detailed logs with the archived daily totals. These totals have no automatic
  expiry. A cleanup retry cannot count the same log twice, and late logs are
  added when they expire. No per-request counter update is needed.
- **Timezone**: each archived bucket stores its original local date and timezone.
  Changing the configured timezone does not shift archived dates; counts sharing
  a date are combined. Retained logs follow the current timezone. Archived data
  has day-level precision, so historical sub-day ranges include the full bucket.
- **Upgrade**: apply Alembic migration `20261007_api_daily_stats` before starting
  the updated web application and scheduler. Existing detailed logs remain
  available and are archived by subsequent cleanups; already-deleted logs cannot
  be reconstructed. Downgrading removes the aggregate history.

---

## Job Scheduling

### Native storage mounts

Native SMB/NFS mounts also use the deployment-only variables
`BSNAP_RECORD_MOUNTS_ENABLED` (default `false`),
`BSNAP_RECORD_MOUNT_ALLOWED_NETWORKS` (default empty; IP/CIDR allowlist), and
`BSNAP_RECORD_NFS_ALLOW_SYS` (default `false`). See [Native recording mounts](record-mounts.md)
for secure Linux deployment prerequisites and protocol requirements.

### record_check_interval_minutes

- **Type**: Integer
- **Default**: `10`
- **Description**: Interval for scanning mounted SMB/NVR recording folders
- **Used In**: `app/jobs/scheduler.py`

### record_check_cron
- **Type**: String (5-part cron)
- **Default**: empty
- **Description**: Optional cron override for `record_folder_check`. Leave empty to use interval scheduling
- **Used In**: `app/jobs/scheduler.py`

### cleanup_cron
- **Type**: String (5-part cron)
- **Default**: `0 2 * * *`
- **Description**: Shared cron schedule for cleanup jobs, including `cleanup_record_checks`
- **Used In**: `app/jobs/scheduler.py`, `app/routes/jobs.py`

---

## WhatsApp Gateway (GoWA)

These values are normally saved through the Config UI and stored in the database configuration table.

### gowa_enabled
- **Type**: Boolean
- **Default**: `0`
- **Description**: Enables WhatsApp notifications through GoWA
- **Used In**: `app/utils/wa_gateway.py`

### gowa_base_url
- **Type**: URL
- **Default**: `http://localhost:3000`
- **Description**: Base URL for the GoWA gateway
- **Example**: `http://172.16.1.200:3434`
- **Used In**: `app/utils/wa_gateway.py`

### gowa_api_key
- **Type**: String
- **Default**: empty
- **Description**: Auth secret for GoWA. Use `user:password` when GoWA runs with `APP_BASIC_AUTH`; use token-only value for older `AUTH_TOKEN` deployments
- **Security**: Keep secure
- **Used In**: `app/utils/wa_gateway.py`

### GOWA_SEND_TIMEOUT_SECONDS
- **Type**: Integer (seconds)
- **Default**: `10`
- **Range**: `1` to `120`
- **Description**: Per-request timeout for outgoing GoWA text and image messages
- **Used In**: `app/utils/wa_gateway.py`

### GOWA_SEND_RETRIES
- **Type**: Integer
- **Default**: `2`
- **Range**: `0` to `5`
- **Description**: Number of retries after a timeout, connection failure, or GoWA server error when sending text and images
- **Used In**: `app/utils/wa_gateway.py`

### WA_CAMERA_THREAD_WORKERS
- **Type**: Integer
- **Default**: `4`
- **Range**: `1` to `16`
- **Description**: Maximum concurrent WhatsApp-triggered camera captures in each web worker process. The existing shared snapshot lock prevents overlapping captures for the same camera across workers.
- **Used In**: `app/utils/wa_executor.py`

### WA_GATEWAY_THREAD_WORKERS
- **Type**: Integer
- **Default**: `8`
- **Range**: `1` to `32`
- **Description**: Maximum concurrent blocking GoWA sends in each web worker process.
- **Used In**: `app/utils/wa_executor.py`

The effective total capacity is the configured pool size multiplied by the number of web worker processes. For example, two web workers and `WA_CAMERA_THREAD_WORKERS=4` allow up to eight captures at once across the instance.

### gowa_default_receiver
- **Type**: String (comma-separated)
- **Default**: empty
- **Description**: Default WhatsApp recipients for reports and alerts. Supports personal numbers and group JIDs
- **Examples**: `6281234567890`, `120363xxxxxxxx@g.us`
- **Used In**: `app/utils/wa_gateway.py`, `app/utils/record_check.py`

### app_public_url
- **Type**: URL
- **Default**: empty
- **Description**: B-SNAP URL reachable by GoWA for generated report files such as the record-check 14-day trend PDF
- **Example**: `http://b-snap:8080` or `http://172.16.1.50:8080`
- **Used In**: `app/utils/record_check_report.py`

---

## Camera & Monitoring

### OFFLINE_ALERT_THRESHOLD_SECONDS
- **Type**: Integer (seconds)
- **Default**: `1800` (30 minutes)
- **Description**: Camera considered offline after this duration
- **Recommendation**: Lower for critical cameras, higher for non-critical
- **Used In**: `app/utils/healthcheck.py`

### CAMERA_TIMEOUT
- **Type**: Integer (seconds)
- **Default**: `10`
- **Description**: Default timeout for camera snapshot operations

---

## Logging Configuration

### LOG_LEVEL
- **Type**: String
- **Default**: `INFO`
- **Options**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Description**: Logging verbosity level
- **Recommendation**: Use `INFO` for production, `DEBUG` for troubleshooting

### LOG_FORMAT
- **Type**: String
- **Default**: `text`
- **Options**: `json`, `text`
- **Description**: Log output format

### BSNAP_LOG_VIA_GUNICORN
- **Type**: Integer (0/1)
- **Default**: `1`
- **Description**: Route logs through gunicorn
- **Recommendation**: Set to `0` for direct logging during development
- **Used In**: `app/main.py`

---

## Server & Performance

### WORKERS
- **Type**: Integer
- **Default**: `2`
- **Description**: Number of gunicorn worker processes
- **Recommendation**: Production: 2-4 x CPU cores

### PORT
- **Type**: Integer
- **Default**: `8080`
- **Description**: Server port

### BIND
- **Type**: String
- **Default**: `0.0.0.0:8080`
- **Description**: Server bind address

### TIMEOUT
- **Type**: Integer (seconds)
- **Default**: `60`
- **Description**: Worker timeout

---

## Timezone & Scheduling

### TZ / SCHEDULER_TZ
- **Type**: String
- **Default**: `Asia/Singapore`
- **Description**: Application timezone

### SCHEDULER_ENABLED
- **Type**: Boolean
- **Default**: `true`
- **Description**: Enable scheduled snapshot jobs

---

## Configuration Priority

B-Snap uses the following priority for configuration:

1. **Environment Variables** (highest priority)
2. **Database Configuration** (via admin UI)
3. **Default Values** (lowest priority)

---

## Production Checklist

### Critical (Must Change)
- [ ] **SECRET_KEY**: Generate new secure key
- [ ] **ENCRYPTION_KEY**: Generate new encryption key
- [ ] **DATABASE_URL**: Use production database with strong password
- [ ] **ENVIRONMENT**: Set to `production`

### Security
- [ ] **DEBUG**: Set to `false`
- [ ] **TRUSTED_HOSTS**: Restrict to specific domains
- [ ] **SESSION_MAX_AGE_SECONDS**: Review timeout setting

### Email
- [ ] **SMTP_HOST**: Configure production SMTP server
- [ ] **SMTP_USER/PASSWORD**: Set valid credentials
- [ ] **EMAIL_FROM**: Set valid sender address

### Retention
- [ ] **RETENTION_SNAPSHOT_DAYS**: Adjust based on storage capacity
- [ ] **RETENTION_VIDEO_DAYS**: Adjust based on storage capacity

### Performance
- [ ] **WORKERS**: Tune based on CPU cores
- [ ] **BIND**: Configure for production network

---

## Environment-Specific Recommendations

### Mining/CCTV Operations (24/7)

```text
# Session (use Remember Me feature instead of extending)
SESSION_MAX_AGE_SECONDS=86400

# Faster offline detection
OFFLINE_ALERT_THRESHOLD_SECONDS=900

# Longer retention for compliance
RETENTION_SNAPSHOT_DAYS=90
RETENTION_VIDEO_DAYS=30

# More workers for high camera count
WORKERS=4
```

### Development

```text
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
BSNAP_LOG_VIA_GUNICORN=0
```

### Testing

```text
ENVIRONMENT=testing
DEBUG=false
DATABASE_URL=postgresql+psycopg2://bsnap_user:pass@localhost:5432/bsnap_test
TESTING=true
```

---

## Troubleshooting

### Common Issues

**Issue**: "SECRET_KEY not set" error  
**Solution**: Set SECRET_KEY in .env file

**Issue**: "Database connection failed"  
**Solution**: Check DATABASE_URL format and credentials

**Issue**: "Encryption key error"  
**Solution**: Ensure ENCRYPTION_KEY is 32-byte hex string

---

## Web Documentation

Access interactive documentation at:

```
http://your-server:8080/docs/environment
```

Features:
- Interactive navigation by category
- Security level indicators
- Usage recommendations
- File references
- Quick reference cards

---

## See Also

- {doc}`security` - Security best practices
- {doc}`deployment` - Deployment guide
- {doc}`maintenance` - Maintenance procedures
