#!/bin/bash
set -e

echo "🛠 Running DB migrations..."
alembic upgrade head

echo "🚀 Starting Gunicorn..."
exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080 --workers 2
