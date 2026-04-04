# AGENTS.md - B-Snap Project Guide

> **Panduan untuk AI Agents** yang berkontribusi pada project B-Snap.

---

## 📋 Project Overview

**B-Snap** adalah aplikasi self-hosted berbasis web untuk mengambil, mengelola, dan memonitor snapshot dari IP Camera yang mendukung ONVIF atau RTSP.

### Tech Stack
- **Backend**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL (production) / SQLite (development)
- **ORM**: SQLAlchemy 2.0 + Alembic
- **Scheduler**: APScheduler
- **Frontend**: Jinja2 Templates + Tailwind CSS
- **Container**: Docker + Docker Compose
- **Web Server**: Gunicorn + Uvicorn

---

## 🚀 Quick Start

Pilih salah satu metode eksekusi:

| Metode | Use Case |
|--------|----------|
| [Docker](#1-run-with-docker) | Production deployment, consistent environment |
| [Local Development](#2-run-without-docker-local-development) | Development, debugging, quick testing |

---

## 1. Run with Docker

### Prerequisites
- Docker 20.10+
- Docker Compose 2.0+

### Steps

1. **Clone repository**
   ```bash
   git clone https://github.com/0x212121/b-snap.git
   cd b-snap
   ```

2. **Setup environment**
   ```bash
   cp .env.example .env
   # Edit .env: Set SECRET_KEY dan konfigurasi lainnya
   ```

3. **Build dan start services**
   ```bash
   docker-compose up -d
   ```

4. **Verify running**
   ```bash
   curl http://localhost:8080/version
   ```

5. **Access application**
   - Web UI: http://localhost:8080
   - pgAdmin: http://localhost:5050

### Services (Docker)
| Service | Port | Description |
|---------|------|-------------|
| b-snap | 8080 | Main web application |
| scheduler | - | Background job scheduler |
| notifier | - | WebSocket notification service |
| postgres | 5432 | PostgreSQL database |
| pgadmin | 5050 | Database admin UI |

### Useful Commands
```bash
# View logs
docker-compose logs -f app

# Run migrations manual
docker-compose exec app alembic upgrade head

# Stop services
docker-compose down

# Full reset (data akan hilang)
docker-compose down -v
```

---

## 2. Run without Docker (Local Development)

### Prerequisites
- Python 3.11+
- Node.js 18+ (untuk Tailwind CSS build)
- FFmpeg (executable di PATH atau di project root)
- PostgreSQL 14+ (opsional, bisa pakai SQLite)

### Steps

1. **Clone repository**
   ```bash
   git clone https://github.com/0x212121/b-snap.git
   cd b-snap
   ```

2. **Create virtual environment**
   ```bash
   python -m venv .venv
   
   # Windows
   .venv\Scripts\activate
   
   # Linux/Mac
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -e ".[dev]"
   ```

4. **Install Node dependencies (untuk CSS)**
   ```bash
   npm install
   npm run build
   ```

5. **Setup environment variables**
   ```bash
   cp .env.example .env
   # Edit .env:
   # - Set SECRET_KEY (wajib)
   # - Database: Gunakan SQLite (default) atau PostgreSQL
   ```

6. **Run database migrations**
   ```bash
   alembic upgrade head
   ```

7. **Start application**
   
   **Windows (PowerShell):**
   ```powershell
   .\start-local.ps1 web
   ```
   
   **Windows (CMD):**
   ```cmd
   start-local.bat web
   ```
   
   **Linux/Mac:**
   ```bash
   # Using Makefile
   make dev
   
   # Atau manual
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

8. **Verify running**
   ```bash
   curl http://localhost:8000/version
   ```

9. **Access application**
   - Web UI: http://localhost:8000
   - Auto-reload: Enabled (server restart saat file berubah)

### Local Startup Options

**Windows PowerShell:**
```powershell
.\start-local.ps1 web       # Production-like server (uvicorn) - web only
.\start-local.ps1 dev       # Development server (uvicorn + reload) - web only
.\start-local.ps1 all       # Start all services (web + scheduler + notifier)
.\start-local.ps1 scheduler # Start scheduler only
.\start-local.ps1 notifier  # Start notifier only
.\start-local.ps1 migrate   # Run migrations only
.\start-local.ps1 check     # Check environment
```

**Windows CMD:**
```cmd
start-local.bat web       # Production-like server
start-local.bat dev       # Development server with auto-reload
start-local.bat scheduler
```

> **Note**: Gunicorn tidak support Windows, jadi di Windows menggunakan `uvicorn` langsung. Gunakan mode `all` untuk menjalankan web server, scheduler, dan notifier dalam satu perintah.

**Linux/Mac (Makefile):**
```bash
make dev              # Development server
make serve            # Production server
make dev-worker       # Scheduler
```

### Database Options (Local)

**SQLite (Default - simplest):**
```env
# .env
DATABASE_URL=sqlite:///./data.db
```

**PostgreSQL (Production-like):**
```env
# .env
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/bsnap_db
```

---

## 📁 Directory Structure

```
b-snap/
├── app/                    # Main application code
│   ├── api/               # API routes (FastAPI routers)
│   ├── core/              # Core config, logging
│   ├── db/                # Database setup
│   ├── jobs/              # Background jobs & scheduler
│   ├── middleware/        # FastAPI middleware
│   ├── models/            # SQLAlchemy models
│   ├── routes/            # Web routes (Jinja2 templates)
│   ├── schemas/           # Pydantic schemas
│   ├── tests/             # Test suite
│   ├── utils/             # Utility functions
│   └── ws/                # WebSocket handlers
├── alembic/               # Database migrations
├── assets/                # Source assets (CSS, JS)
├── docs/                  # Sphinx documentation
├── static/                # Static files (served)
├── templates/             # Jinja2 templates
├── logs/                  # Application logs
├── pyproject.toml         # Modern Python project config
├── pytest.ini            # Test configuration
├── Makefile              # Development commands
├── docker-compose.yml    # Docker services
├── start.sh              # Docker startup script
├── start-local.ps1       # Windows local startup
└── start-local.bat       # Windows CMD local startup
```

---

## 🔧 Development Commands

Gunakan `Makefile` (Linux/Mac) atau `start-local.ps1` (Windows) untuk commands:

```bash
# Development Server
make dev                    # Run dev server with hot reload
make serve                  # Run production server

# Testing
make test                   # Run all tests
make test-unit              # Run unit tests only
make test-integration       # Run integration tests
make test-coverage          # Run tests with coverage

# Code Quality
make lint                   # Run all linters
make format                 # Format code (black + ruff)
make check-format           # Check formatting

# Database
make db-migrate             # Run migrations
make db-makemigrations      # Create new migration
make db-reset               # Reset database

# Utilities
make clean                  # Clean build artifacts
```

---

## 🧪 Testing Guidelines

### Test Structure
```
app/tests/
├── conftest.py          # Shared fixtures
├── unit/               # Unit tests
│   ├── test_models/
│   ├── test_utils/
│   └── test_services/
└── integration/        # Integration tests
    ├── test_api/
    └── test_camera/
```

### Running Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=html

# Specific marker
pytest -m unit          # Unit tests only
pytest -m integration   # Integration tests
pytest -m "not slow"    # Skip slow tests
```

---

## 📝 Code Standards

### Python Style
- **Formatter**: Black (line length: 100)
- **Linter**: Ruff (replaces flake8, isort, pydocstyle)
- **Type Checker**: mypy (strict mode)
- **Security**: bandit

### Import Order
```python
# 1. Standard library
import os
from datetime import datetime

# 2. Third-party
from fastapi import FastAPI
from sqlalchemy import Column

# 3. Local application
from app.db.database import Base
from app.models.user import User
```

### Type Hints
- Selalu gunakan type hints untuk function arguments dan return values
- Gunakan `from __future__ import annotations` untuk Python 3.11+

### Docstrings (Google Style)
```python
def get_camera_snapshot(camera_id: int, timeout: int = 10) -> bytes:
    """Capture snapshot from camera.
    
    Args:
        camera_id: The ID of the camera to capture from.
        timeout: Maximum time to wait for snapshot in seconds.
        
    Returns:
        Raw bytes of the captured image.
        
    Raises:
        CameraNotFoundError: If camera doesn't exist.
    """
```

---

## 🔒 Security Best Practices

1. **Never commit secrets**: Gunakan `.env` file
2. **Validate inputs**: Gunakan Pydantic schemas
3. **SQL Injection**: Gunakan SQLAlchemy ORM
4. **XSS Protection**: Escape output di templates Jinja2
5. **CSRF**: Session-based auth include protection
6. **Dependencies**: Jalankan `safety check` dan `bandit`

---

## 🔔 SweetAlert2 (Alerts & Notifications)

Project menggunakan **SweetAlert2** untuk semua modal, alert, dan konfirmasi. Tidak lagi menggunakan native `alert()` atau `confirm()`.

### Setup

SweetAlert2 di-install via NPM dan disimpan di static folder untuk offline support:

```bash
npm install sweetalert2
```

Files:
- `static/js/sweetalert2.min.js`
- `static/css/sweetalert2.min.css`

### Global Functions (base.html)

Sudah tersedia global function yang bisa dipakai di semua template:

```javascript
// Confirmation dialog
showGlobalConfirm(message, onConfirm, options);

// Alert dialog
showGlobalAlert(message, title, options);

// Themed Swal (untuk custom Swal dengan dark mode support)
themedSwal({
  icon: 'success',
  title: 'Success',
  text: 'Operation completed'
});
```

### Usage Examples

**Basic Confirmation:**
```javascript
showGlobalConfirm('Delete this camera?', () => {
    // Execute delete
    deleteCamera(id);
}, {
    title: 'Confirm Delete',
    confirmText: 'Delete',
    confirmButtonColor: '#dc2626'  // red for danger
});
```

**SweetAlert2 dengan Dark Mode Support:**
```javascript
// Gunakan themedSwal() untuk otomatis mendukung dark mode
themedSwal({
    icon: 'success',
    title: 'Success',
    text: 'Camera added successfully',
    timer: 1500,
    showConfirmButton: false
});

themedSwal({
    icon: 'error',
    title: 'Error',
    text: 'Failed to load camera data'
});

themedSwal({
    icon: 'warning',
    title: 'Validation Error',
    text: 'Please select both start and end dates'
});
```

### Dark Mode Support

SweetAlert2 otomatis mengikuti tema dark/light mode melalui helper function `getSwalThemeConfig()` dan `themedSwal()` di `base.html`:

```javascript
// Helper: Get current theme config
function getSwalThemeConfig() {
  const isDark = document.documentElement.classList.contains('dark');
  return {
    background: isDark ? '#1f2937' : '#ffffff',
    color: isDark ? '#f3f4f6' : '#111827',
    confirmButtonColor: '#2563eb',
    cancelButtonColor: isDark ? '#4b5563' : '#9ca3af',
    iconColor: isDark ? '#60a5fa' : '#3b82f6',
  };
}

// Helper: Create themed Swal instance
function themedSwal(options = {}) {
  return Swal.fire({...getSwalThemeConfig(), ...options});
}
```

**Catatan:** Selalu gunakan `themedSwal()` alih-alih `Swal.fire()` langsung agar dark mode berfungsi dengan baik.

### Icon Types

- `success` - Green checkmark (success operations)
- `error` - Red X (errors, failures)
- `warning` - Yellow triangle (validation, caution)
- `info` - Blue info (informational)
- `question` - Blue question mark (choices)

### Files yang sudah pakai SweetAlert2

| Template | Usage |
|----------|-------|
| `recipients.html` | CRUD operations, CSV import, test email |
| `cameras.html` | Snapshot download errors |
| `health_history.html` | Date validation |
| `insights.html` | Report generation errors |
| `nvrs.html` | Delete confirmations |
| `snapshot_gallery.html` | Bulk delete confirmation |

---

## 📧 Notification System - Important Notes

### Incident Time Accuracy (Critical Fix)

**Issue**: Tamper alert emails previously showed incorrect incident times (email send time instead of actual detection time).

**Fix**: The `send_tamper_alert()` function now accepts an `incident_time` parameter:

```python
def send_tamper_alert(
    db: Session, 
    camera, 
    reason: str, 
    snapshot_path: str, 
    incident_time: datetime = None  # NEW PARAMETER
) -> bool:
```

**Usage:**
```python
# When triggering from snapshot detection, pass the snapshot timestamp:
from app.utils.email_notifier import send_tamper_alert

send_tamper_alert(
    db, 
    camera, 
    snapshot.tamper_reason, 
    file_path,
    incident_time=snapshot.timestamp  # Pass actual detection time
)
```

**Key Points:**
- Always pass `snapshot.timestamp` when calling from snapshot detection
- The function defaults to `datetime.now()` if not provided (for backwards compatibility)
- Both email body and database log entries use the provided `incident_time`
- Recovery alerts don't need this as they represent current events

### Anti-Flooding Protection

The notification system has 4-layer protection:

1. **Scheduler Rate Limit**: `MIN_SNAPSHOT_INTERVAL_SECONDS = 30` (30s between snapshots)
2. **Consecutive Counter Check**: `TAMPER_CONFIRM_THRESHOLD = 3` (3 tamper snapshots before alert)
3. **Cooldown Mechanism**: `ALERT_COOLDOWN_MINUTES = 15` (15-min deduplication window)
4. **Circuit Breaker**: 5 failures → 1 hour suppression

Cooldown is set **immediately** upon entering `send_tamper_alert()`, before SMTP check, to prevent flooding even on SMTP failures.

---

## 📚 Database Migrations

Menggunakan Alembic untuk database migrations:

```bash
# Generate new migration
alembic revision --autogenerate -m "add users table"

# Apply migrations
alembic upgrade head

# Downgrade one revision
alembic downgrade -1

# View current version
alembic current
```

---

## 🎯 Common Tasks

### Add New Model
1. Buat model di `app/models/`
2. Import di `app/models/__init__.py`
3. Generate migration: `make db-makemigrations message="add model"`
4. Apply: `make db-migrate`

### Add New Route
1. Buat router di `app/routes/`
2. Import dan register di `app/main.py`

### Add Background Job
1. Buat function di `app/jobs/`
2. Register di `app/jobs/scheduler.py`

---

## 🐛 Debugging

```bash
# Enable debug logging
LOG_LEVEL=DEBUG make dev

# Run with pdb
python -m pdb -m uvicorn app.main:app

# Memory profiling
python -m memray run app/main.py
```

---

## 📖 Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/)
- [Alembic](https://alembic.sqlalchemy.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [ONVIF Specs](https://www.onvif.org/profiles/specifications/)

---

**Version**: 1.16.0  
**Last Updated**: 2026-03-28

---

## 🔒 Security Features (P0/P1/P2)

### Evidence Integrity (P0-001)
- SHA-256 hash untuk semua snapshot dan video
- Endpoint: `GET /snap/{id}/verify` untuk verifikasi integritas
- Kolom: `snapshots.file_hash`, `videos.file_hash`

### Soft Delete (P0-002)
- Data dihapus = soft delete (flag `deleted_at`)
- Trash Management: `/admin/trash`
- Restore: `POST /snap/{id}/restore`, `POST /videos/{id}/restore`
- Purge: `POST /admin/snapshots/purge`, `POST /admin/videos/purge`

### Password Encryption (P1-001)
- Algorithm: AES-256-GCM
- Key: `ENCRYPTION_KEY` environment variable (32-byte hex)
- Format: `ENC:<base64>`

### Append-Only Audit Log (P2-001)
- Database triggers mencegah UPDATE/DELETE pada `audit_logs`
- Enhanced columns: `user_agent`, `request_path`, `request_method`, `response_status`
- Functions: `log_audit()`, `log_api_access()`

### Safety Classification (P2-002)
- Klasifikasi kamera: `critical`, `standard`, `low`
- Kolom: `cameras.safety_classification`
- Untuk mining safety compliance

### Retention Hold (P2-003)
- Legal hold untuk footage kritis
- Kolom: `retention_hold`, `retention_hold_reason`, `retention_hold_by`, `retention_hold_at`
- Purge otomatis skip items dengan retention hold

### Secure Snapshot Serving (P2-004)
- Direct access ke `/static/snapshots/*` → 403 Forbidden
- **Anti-Flooding Audit Log:**
  - Gallery view: `POST /api/snapshots/gallery-view` (batch log)
  - Thumbnail: `GET /api/snapshots/secure/{id}?thumb=true` (minimal log)
  - Detail view: `GET /api/snapshots/secure/{id}` (full log)
- API endpoints dengan autentikasi:
  - `GET /api/snapshots/file/{file_path}`
  - `GET /api/snapshots/secure/{snapshot_id}`
- Download: `GET /api/snapshots/secure/{id}?download=true`
- Semua akses dilog ke audit_logs dengan konteks yang sesuai
