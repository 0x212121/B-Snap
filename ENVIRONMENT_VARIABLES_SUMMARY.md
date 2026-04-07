# B-Snap Environment Variables Summary

## Overview
- **Total Variables**: 40+
- **Categories**: 10
- **Documentation URL**: `/docs/environment`
- **Template File**: `.env.example`

## Categories

### 1. Core Application (4 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | YES | - | Session & CSRF protection |
| `ENCRYPTION_KEY` | YES | - | AES-256 encryption for camera passwords |
| `ENVIRONMENT` | YES | development | App mode: development/testing/production |
| `DATABASE_URL` | YES | - | PostgreSQL connection URL |

### 2. Session & Auth (2 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SESSION_MAX_AGE_SECONDS` | NO | 86400 | Session timeout (24 hours) |
| `MAX_WEB_SESSIONS` | NO | 1 | Max concurrent sessions per user |

### 3. Security (2 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TRUSTED_HOSTS` | NO | * | CORS trusted hosts |
| `DEBUG` | NO | false | Debug mode (NEVER in production) |

### 4. Email SMTP (7 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SMTP_HOST` | NO | smtp.gmail.com | SMTP server |
| `SMTP_PORT` | NO | 587 | SMTP port |
| `SMTP_USER` | NO | - | SMTP username |
| `SMTP_PASSWORD` | NO | - | SMTP password |
| `EMAIL_FROM` | NO | - | Sender email |
| `EMAIL_CC` | NO | - | CC addresses |

### 5. Storage & Paths (5 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SNAPSHOT_PATH` | NO | ./static/snapshots | Snapshot storage |
| `VIDEO_PATH` | NO | ./static/videos | Video storage |
| `LOG_DIR` | NO | ./logs | Log files directory |
| `ALEMBIC_INI_PATH` | NO | ./alembic.ini | Migration config |

### 6. Retention Policy (2 vars) - MED-003
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RETENTION_SNAPSHOT_DAYS` | NO | 30 | Auto-delete snapshots after N days |
| `RETENTION_VIDEO_DAYS` | NO | 7 | Auto-delete videos after N days |

### 7. Camera & Monitoring (2 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OFFLINE_ALERT_THRESHOLD_SECONDS` | NO | 1800 | Camera offline threshold (30 min) |
| `CAMERA_TIMEOUT` | NO | 10 | Snapshot timeout (seconds) |

### 8. Logging (7 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOG_LEVEL` | NO | INFO | Log verbosity |
| `LOG_DIR` | NO | ./logs | Log directory |
| `LOG_FORMAT` | NO | text | json or text |
| `BSNAP_LOG_VIA_GUNICORN` | NO | 1 | Route logs through gunicorn |
| `LOG_MAX_SIZE_MB` | NO | 10 | Max log file size |
| `LOG_MAX_BACKUPS` | NO | 7 | Log file retention |

### 9. Server (5 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WORKERS` | NO | 2 | Gunicorn workers |
| `PORT` | NO | 8080 | Server port |
| `BIND` | NO | 0.0.0.0:8080 | Bind address |
| `TIMEOUT` | NO | 60 | Worker timeout |

### 10. Timezone (2 vars)
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TZ` | NO | Asia/Singapore | Application timezone |
| `SCHEDULER_ENABLED` | NO | true | Enable scheduled jobs |

## Usage

### Quick Setup
```bash
# 1. Copy template
cp .env.example .env

# 2. Generate secure keys
export SECRET_KEY=$(openssl rand -hex 32)
export ENCRYPTION_KEY=$(openssl rand -hex 32)

# 3. Edit .env with your values
nano .env
```

### Production Checklist
- [ ] Change SECRET_KEY
- [ ] Change ENCRYPTION_KEY
- [ ] Set ENVIRONMENT=production
- [ ] Set DEBUG=false
- [ ] Restrict TRUSTED_HOSTS
- [ ] Configure SMTP
- [ ] Adjust RETENTION_* settings

## Recommendations

### For Mining/CCTV Operations
```env
# Longer session for 24/7 monitoring (with Remember Me)
SESSION_MAX_AGE_SECONDS=86400

# Faster offline detection
OFFLINE_ALERT_THRESHOLD_SECONDS=900

# Longer retention for compliance
RETENTION_SNAPSHOT_DAYS=90
RETENTION_VIDEO_DAYS=30

# More workers for high camera count
WORKERS=4
```

### For Development
```env
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
BSNAP_LOG_VIA_GUNICORN=0
```

## Documentation

Access the web documentation at: `http://your-server:8080/docs/environment`

Features:
- Interactive navigation
- Security level indicators
- Usage recommendations
- File references
- Quick reference cards
