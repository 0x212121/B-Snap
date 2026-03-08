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

### Prerequisites
- Python 3.11+
- PostgreSQL 14+ (atau SQLite untuk dev)
- Node.js 18+ (untuk Tailwind CSS)
- FFmpeg

### Installation

```bash
# 1. Clone repository
git clone https://github.com/0x212121/b-snap.git
cd b-snap

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
make install-dev
# atau: pip install -r requirements.txt && pip install -r requirements-dev.txt

# 4. Install pre-commit hooks
make install-pre-commit

# 5. Setup environment
cp .env.example .env
# Edit .env sesuai konfigurasi lokal

# 6. Run database migrations
make db-migrate

# 7. Build Tailwind CSS
npm install
npm run build

# 8. Start development server
make dev
```

Akses aplikasi di: http://localhost:8000

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
└── docker-compose.yml    # Docker services
```

---

## 🔧 Development Commands

Gunakan `Makefile` untuk commands yang umum:

```bash
# Development
make dev              # Run dev server with hot reload
make install-dev      # Install dev dependencies

# Testing
make test             # Run all tests
make test-unit        # Run unit tests
make test-integration # Run integration tests
make test-coverage    # Run tests with coverage

# Code Quality
make lint             # Run all linters
make format           # Format code (black + ruff)
make check-format     # Check formatting without changes
make pre-commit       # Run pre-commit hooks

# Database
make db-migrate       # Run migrations
make db-makemigrations message="add users table"  # Create migration
make db-reset         # Reset database

# Docker
make docker-build     # Build Docker image
make docker-up        # Start containers
make docker-down      # Stop containers

# Utilities
make clean            # Clean build artifacts
make help             # Show all commands
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

# Parallel execution
pytest -n auto          # Use all CPU cores
```

### Writing Tests
- Gunakan fixtures dari `conftest.py`
- Mock external services (camera, email, etc.)
- Gunakan marker `@pytest.mark.unit` atau `@pytest.mark.integration`
- Target coverage: minimal 70%

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
- Gunakan `Optional`, `Union`, `List` dari `typing` atau `|` syntax

### Docstrings
- Gunakan Google-style docstrings
- Document semua public modules, classes, dan functions

Example:
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
        SnapshotTimeoutError: If capture times out.
    """
```

---

## 🔒 Security Best Practices

1. **Never commit secrets**: Gunakan `.env` file
2. **Validate inputs**: Gunakan Pydantic schemas
3. **SQL Injection**: Gunakan SQLAlchemy ORM, jangan raw SQL
4. **XSS Protection**: Escape output di templates Jinja2
5. **CSRF**: Session-based auth sudah include protection
6. **Dependencies**: Jalankan `safety check` dan `bandit` secara regular

---

## 🐳 Docker Development

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f app

# Run migrations
docker-compose exec app alembic upgrade head

# Rebuild after changes
docker-compose build --no-cache
```

Services:
- `app`: Main web application
- `scheduler`: Background job scheduler
- `notifier`: WebSocket notification service
- `postgres`: PostgreSQL database
- `pgadmin`: Database admin UI

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

# History
alembic history --verbose
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

### Add Static File
1. Place di `static/` folder
2. Reference di template: `/static/path/to/file`

---

## 🐛 Debugging

```bash
# Enable debug logging
LOG_LEVEL=DEBUG make dev

# Run with pdb
python -m pdb -m uvicorn app.main:app

# Memory profiling
python -m memray run app/main.py

# CPU profiling
py-spy top -- python app/main.py
```

---

## 📖 Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2.0](https://docs.sqlalchemy.org/)
- [Alembic](https://alembic.sqlalchemy.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [ONVIF Specs](https://www.onvif.org/profiles/specifications/)

---

## 💬 Questions?

- Check `docs/` directory untuk dokumentasi lengkap
- Review `Backlog.md` untuk feature roadmap
- Lihat `CHANGELOG.md` untuk update terbaru

---

**Version**: 1.12.0  
**Last Updated**: 2024
