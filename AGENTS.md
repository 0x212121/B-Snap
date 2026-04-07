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

## 🍞 Toast Notifications (Toast.js)

Project menggunakan **Toast.js** (via `static/js/toast.js`) untuk menampilkan notifikasi non-blocking. Toast digunakan untuk feedback setelah operasi AJAX/CRUD.

### Setup

Toast.js sudah tersedia global di `base.html`:

```javascript
// Success toast
Toast.success('Operation completed', { title: 'Success', duration: 3000 });

// Error toast
Toast.error('Something went wrong', { title: 'Error', duration: 5000 });

// Info toast
Toast.info('Please wait...', { title: 'Info' });

// Warning toast
Toast.warning('Check your input', { title: 'Warning' });
```

### AJAX Form Submission Best Practice

Ketika membuat form dengan AJAX submission, **PENTING** untuk mengirim header `Accept: application/json` agar backend mengembalikan JSON response, bukan HTML page.

**❌ INCORRECT - Will cause "Unexpected token '<'" error:**
```javascript
const response = await fetch('/profile/change-password', {
    method: 'POST',
    body: formData  // Missing Accept header!
});
const result = await response.json();  // Error: HTML returned instead of JSON
```

**✅ CORRECT:**
```javascript
const response = await fetch('/profile/change-password', {
    method: 'POST',
    headers: { 'Accept': 'application/json' },  // Required!
    body: formData
});
const result = await response.json();  // Works: JSON returned
```

### Backend Response Format

Backend menggunakan header `Accept` untuk mendeteksi AJAX requests:

```python
@router.post("/some-endpoint")
async def some_endpoint(request: Request, ...):
    # Check if request wants JSON (AJAX)
    if request.headers.get("Accept") == "application/json":
        return JSONResponse({
            "status": "success",
            "message": "Operation completed"
        })
    
    # Otherwise return HTML page (traditional form submission)
    return templates.TemplateResponse("page.html", {...})
```

### Complete AJAX Form Example

```javascript
async function submitForm(formData) {
    try {
        const response = await fetch('/api/endpoint', {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: formData
        });
        
        const result = await response.json();
        
        if (response.ok && result.status === 'success') {
            Toast.success(result.message, { title: 'Success' });
            // Update UI or refresh data
        } else {
            Toast.error(result.message || 'Operation failed', { title: 'Error' });
        }
    } catch (error) {
        console.error('Error:', error);
        Toast.error('Network error. Please try again.', { title: 'Error' });
    }
}
```

### Common Toast Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `SyntaxError: Unexpected token '<'` | Server returned HTML instead of JSON | Add `headers: { 'Accept': 'application/json' }` to fetch |
| `Network error` | CORS issue or server unreachable | Check network connection and server status |
| Toast not showing | Toast.js not loaded | Check if `toast.js` is included in base.html |

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

## 👥 Online Users Tracking

Fitur untuk melacak user yang sedang online. Hanya admin yang dapat melihat informasi ini.

### Components

**Backend:**
- `app/utils/online_users.py` - Thread-safe tracker untuk user online
- `app/middleware/online_user_tracker.py` - Middleware untuk update activity
- `app/routes/online_users.py` - API endpoints

**Frontend:**
- Sticky popup di `templates/base.html` (hanya untuk admin)
- Auto-refresh setiap 30 detik

### API Endpoints

| Endpoint | Method | Access | Description |
|----------|--------|--------|-------------|
| `/api/online-users` | GET | Admin | Get detailed list of online users |
| `/api/online-users/count` | GET | Admin | Get count only (lightweight) |

### How It Works

1. **Tracking**: Middleware `OnlineUserTrackerMiddleware` memperbarui timestamp setiap request
2. **Timeout**: User dianggap offline setelah 5 menit tidak ada aktivitas
3. **Logout**: User dihapus dari tracking saat logout
4. **Display**: Sticky popup menampilkan jumlah user online dan detail saat diklik

### Usage Example (Backend)

```python
from app.utils.online_users import get_online_tracker

tracker = get_online_tracker()

# Get online users
users = tracker.get_online_users()
count = tracker.get_online_count()
stats = tracker.get_stats()
```

---

## 📖 Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/)
- [Alembic](https://alembic.sqlalchemy.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [ONVIF Specs](https://www.onvif.org/profiles/specifications/)

---

**Version**: 1.17.0  
**Last Updated**: 2026-04-07

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

---

## 🔒 Critical Security Fixes (CRIT)

### Secure Video Serving (CRIT-001)
- **Issue:** Video files bisa diakses langsung via `/static/videos/*` tanpa autentikasi
- **Fix:** 
  - Direct access ke `/static/videos/*` → 403 Forbidden
  - API endpoints dengan autentikasi:
    - `GET /api/videos/file/{file_path}`
    - `GET /api/videos/secure/{video_id}`
  - Download: `GET /api/videos/secure/{id}?download=true`
  - Batch logging: `POST /api/videos/gallery-view`
  - Semua akses dilog ke audit_logs
- **Files Modified:** `app/main.py`, `app/routes/videos.py`, `templates/video_gallery.html`, `templates/_video_grid.html`

### Group-Based Access Control Fix (CRIT-002)
- **Issue:** Group filtering menggunakan camera name matching (`Snapshot.camera_group == user_group.name`) yang bisa menyebabkan akses tidak sah jika nama grup berubah
- **Fix:** Menggunakan foreign key relationship via JOIN dengan Camera table:
  ```python
  db.query(Snapshot).join(
      Camera, Snapshot.camera_id == Camera.id, isouter=True
  ).filter(Camera.group_id == group_id)
  ```
- **Files Modified:** `app/routes/snap_gallery.py`, `app/routes/videos.py`

### CSV Import Validation (CRIT-003)
- **Issue:** CSV import tidak memvalidasi safety_classification dengan ketat dan tidak warning untuk password lemah
- **Fix:**
  - Validasi strict untuk safety_classification (critical/standard/low)
  - Skip row dengan invalid safety_classification
  - Log warning untuk "critical" classification (require verification)
  - Log warning untuk password yang terlalu lemah (< 4 karakter)
- **Files Modified:** `app/routes/cameras.py`

---

## 🔧 Medium Security Fixes (MED)

### Session Cookie Consistency (MED-001)
- **Issue:** Session token max_age (24 jam) tidak konsisten dengan expires_at (30 hari)
- **Fix:**
  - Align token expiration dengan cookie max_age (24 jam untuk mining environment)
  - Configurable via `SESSION_MAX_AGE_SECONDS` environment variable
  - Default: 86400 seconds (24 hours)
  - **Remember Me Integration:** Session di-auto-refresh untuk user dengan Remember Me aktif
    - Memungkinkan CCTV monitoring 24/7 tanpa manual re-login
    - Session tetap 24 jam tapi di-refresh otomatis setiap request
    - Remember Me token tetap 1 tahun
- **Files Modified:** `app/routes/auth.py`, `app/middleware/auth_and_setup.py`

### API Token Expiration Enforcement (MED-002)
- **Issue:** API tokens tanpa expires_at diterima indefinitely
- **Fix:**
  - Tokens tanpa expiration ditolak dengan error 401
  - Token generation memerlukan expires_in_days > 0
  - Clear error message: "Token has no expiration. Please generate a new token."
- **Files Modified:** `app/routes/auth.py`, `app/routes/user_management.py`

### Automated Retention Policy (MED-003)
- **Issue:** `MAX_SNAPSHOT_AGE_DAYS` dan `MAX_VIDEO_AGE_DAYS` ada di config tapi tidak di-enforce
- **Fix:**
  - New scheduled job: `retention_policy` - runs daily at 3 AM
  - Soft-deletes snapshots/videos older than retention period
  - Items dengan `retention_hold=True` di-skip
  - Configurable via:
    - `retention_snapshot_days` (default: 30)
    - `retention_video_days` (default: 7)
- **Files Modified:** `app/jobs/scheduler.py`

### GPS Coordinate Validation (MED-004)
- **Issue:** GPS coordinates tidak divalidasi untuk valid ranges
- **Fix:**
  - Latitude validation: -90 to 90
  - Longitude validation: -180 to 180
  - SQLAlchemy `@validates` decorator pada model
  - Raises ValueError untuk invalid coordinates
- **Files Modified:** `app/models/camera.py`

---

## 🟢 Low Fixes (LOW)

### Remove Default Group "ALL" References (LOW-002)
- **Issue:** Group "ALL" sudah dihapus dari database tapi masih ada di `default_groups`
- **Fix:**
  - Removed `{"id": 11, "name": "ALL"}` dari `default_groups` list
  - Added comment explaining NULL group_id gives access to all cameras
  - Migration `20260330_remove_all_group.py` sudah handle database cleanup
- **Files Modified:** `app/models/camera_group.py`
