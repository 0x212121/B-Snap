#!/bin/bash
set -euo pipefail

# Pindah ke folder tempat script ini berada (misalnya ./app/run.sh)
cd "$(dirname "$0")"
cd ..  # ke root project (harusnya di sinilah alembic.ini berada)

# Load environment variables dari .env
if [ -f .env ]; then
  echo "📦 Loading environment from .env"
  export $(grep -v '^#' .env | xargs)
fi

# Default to 2 workers if not set
: "${WORKERS:=2}"

echo "🛠 Running Alembic migrations..."
alembic -c alembic.ini upgrade head

echo "🚀 Starting Gunicorn with $WORKERS workers..."
exec gunicorn app.main:app \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8080 \
  --workers "$WORKERS" \
  --worker-connections 1000 \
  --timeout 60 \
  --keep-alive 2
