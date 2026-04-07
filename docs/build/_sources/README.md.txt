# B-Snap Sphinx Documentation

## Overview

This directory contains the Sphinx documentation source for B-Snap.

**Build Output**: `docs/build/`  
**Source Files**: `docs/source/`  
**Configuration**: `docs/source/conf.py`

## Documentation Files

| File | Description |
|------|-------------|
| `index.rst` | Main documentation index and toctree |
| `architecture.md` | System architecture overview |
| `configuration.md` | **Environment variables documentation (NEW)** |
| `api.md` | **API reference documentation (NEW)** |
| `security.md` | Security features and best practices |
| `audit_logging.md` | Audit logging system (P2-001) |
| `notifications.md` | Email and WhatsApp notifications |
| `development.md` | Development setup and guidelines |
| `deployment.md` | Production deployment guide |
| `maintenance.md` | Maintenance procedures |
| `appendix.md` | Additional reference material |

## Building Documentation

### Prerequisites

```bash
pip install -r requirements-dev.txt
```

### Build HTML

```bash
cd docs
make html
```

### View Documentation

```bash
# After building
open build/html/index.html
```

Or serve via Python:

```bash
cd docs/build/html
python -m http.server 8000
```

Then open: http://localhost:8000

## Environment Variables Documentation

The environment variables documentation is available at:

- **Sphinx Docs**: `configuration.md` (this directory)
- **Web UI**: `/docs/environment` (application endpoint)
- **Template**: `.env.example` (project root)

### Categories

1. **Core Settings** - SECRET_KEY, ENCRYPTION_KEY, DATABASE_URL
2. **Session & Auth** - SESSION_MAX_AGE_SECONDS, MAX_WEB_SESSIONS
3. **Security** - TRUSTED_HOSTS, DEBUG
4. **Email** - SMTP_HOST, SMTP_USER, etc.
5. **Storage** - SNAPSHOT_PATH, VIDEO_PATH
6. **Retention** - RETENTION_SNAPSHOT_DAYS, RETENTION_VIDEO_DAYS
7. **Camera** - OFFLINE_ALERT_THRESHOLD_SECONDS
8. **Logging** - LOG_LEVEL, LOG_DIR
9. **Server** - WORKERS, PORT, BIND
10. **Timezone** - TZ, SCHEDULER_ENABLED

## API Documentation

API endpoints documentation includes:

- Authentication (Bearer Token)
- Snapshots API (CRIT-001)
- Videos API (CRIT-001)
- Camera API
- Retention Hold API (P2-003)
- Trash Management
- User Management
- Audit API (P2-001)

## Updates

**Last Updated**: 2026-04-07

### Recent Additions
- Environment variables documentation (`configuration.md`)
- API reference (`api.md`)
- Security fixes documentation (CRIT, MED, LOW)
