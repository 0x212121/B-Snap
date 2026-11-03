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
use disposable schemas independently of the shared database fixture:

```bash
python -m pytest app/tests/test_database_migration.py -o addopts="" -q
```


## Safe automated tests (CA-12)

Run from the repository root with the project Python environment:

```powershell
python -m pytest --collect-only -q
python -m pytest -q
```

`pytest.ini` is the single configuration source. Test bootstrap overrides
`DATABASE_URL` with a disabled loopback address before importing application code;
application-engine connections and unmocked external DNS/socket connections fail.
Loopback servers used by mocked transport tests remain available. HTTP client
fixtures use ASGITransport without starting application lifespan, scheduler or
capture workers. Tests requiring actual lifecycle behavior must mock their
transports and database explicitly.

Mocked/unit tests run without PostgreSQL. Database cases skip with an explicit
reason unless the operator supplies a **dedicated disposable test PostgreSQL**:

```powershell
$env:BSNAP_TEST_DATABASE_URL = 'postgresql+psycopg2://bsnap_test@127.0.0.1:55432/bsnap_test'
$env:BSNAP_TEST_POSTGRES_URL = $env:BSNAP_TEST_DATABASE_URL
python -m pytest -q
```

Supply credentials through the local environment when required. Never use the
application `DATABASE_URL` for this command. The test user must be able to create
and drop schemas. Shared ORM tests create a unique `pytest_<uuid>` schema, retain
PostgreSQL JSONB and audit triggers, and wrap each test in an outer transaction
with session savepoints; application commits are rolled back at teardown. Migration
and concurrency tests create their own unique schemas. All schemas are dropped
on successful or failed test teardown. Hard termination can require removing the
identified disposable schemas manually. Parallel runs must use separate schemas;
do not point concurrent jobs at application data.

The shared ORM fixture uses model DDL; separate migration tests exercise Alembic
upgrade/downgrade against PostgreSQL. Passing unit tests without the explicit
PostgreSQL URLs does not validate migrations, triggers or concurrency.

## Legacy manual diagnostics

Eight historical scripts now live under `scripts/diagnostics/`, outside pytest's
test paths. Importing them does not run their old module-level operations. They
are retained as manual legacy tools, not automated tests or current operational
recipes. Some reference container-only paths or old APIs; inspect them first.
They may read/modify deployment data or send notifications when explicitly run:

```powershell
python -m scripts.diagnostics.group_access --allow-live-operations
```

The flag is mandatory even for read-only legacy checks. Automated tests never
invoke that flag. No real SMTP, GoWA, camera capture or production scheduler is
required by the automated suite.
