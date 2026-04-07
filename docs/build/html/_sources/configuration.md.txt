# Configuration Guide

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

---

## Storage & Paths

### SNAPSHOT_PATH
- **Type**: String (path)
- **Default**: `./static/snapshots`
- **Description**: Directory for snapshot storage

### VIDEO_PATH
- **Type**: String (path)
- **Default**: `./static/videos`
- **Description**: Directory for video storage

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

### RETENTION_SNAPSHOT_DAYS
- **Type**: Integer
- **Default**: `30`
- **Description**: Snapshots older than this are soft-deleted
- **Note**: Items with `retention_hold=True` are preserved
- **Used In**: `app/jobs/scheduler.py`

### RETENTION_VIDEO_DAYS
- **Type**: Integer
- **Default**: `7`
- **Description**: Videos older than this are soft-deleted
- **Note**: Items with `retention_hold=True` are preserved
- **Used In**: `app/jobs/scheduler.py`

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
