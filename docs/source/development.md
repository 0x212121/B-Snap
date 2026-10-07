# Development Guide

## Prerequisites
- **Python** ≥ 3.13  
- **PostgreSQL** ≥ 17.5  
- **n8n** (required for WhatsApp integration)  
- **Node.js + npm** (for Tailwind CSS build)  

## Setup

```bash
# masuk ke project
cd b-snap
```

## Buat virtual environment
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```
## install dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Frontend (Tailwind CSS)
```bash

# install dependencies (once)
npm install -D tailwindcss@3.4.1 postcss autoprefixer

# run watcher (rebuild CSS on change)
npm run dev
```

## Tips

Simpan .env untuk variabel seperti DB connection, API key, dsb.

Jangan commit .env → tambahkan ke .gitignore.

Gunakan pre-commit hooks (optional) untuk linting/formatting sebelum push.
"""

## Database initialization

Before starting web or scheduler directly, apply migrations and seed missing
configuration keys on the development PostgreSQL database:

```bash
python -m app.db.migrate
```

On Windows, `start-local.ps1 web`, `dev`, and `all` run this command before
starting services; `start-local.ps1 migrate` runs it on its own. Failures stop
startup. Direct `uvicorn`/Gunicorn startup never creates tables or runs migrations.

Migration integration tests require an isolated PostgreSQL test database via
`BSNAP_TEST_DATABASE_URL`. They create and drop unique temporary schemas and
override the global SQLite schema fixture:

```bash
python -m pytest app/tests/test_database_migration.py -o addopts="" -q
```
