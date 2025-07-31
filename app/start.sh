#!/bin/bash
set -e

# Load environment variables from .env manually (kalau tidak otomatis loaded)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Default to 2 workers if not set
: "${WORKERS:=2}"

echo "🛠 Running Alembic migrations..."
alembic upgrade head

echo "🚀 Starting Gunicorn with $WORKERS workers..."
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8080 \
  --workers "$WORKERS"
