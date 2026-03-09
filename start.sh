#!/bin/bash
# =============================================================================
# B-Snap Startup Script
# =============================================================================
# This script initializes and starts the B-Snap application.
# It handles database migrations, static file collection, and server startup.
#
# Usage:
#   ./start.sh [web|worker|scheduler|all]
#
# Modes:
#   web       - Start web server only (gunicorn)
#   worker    - Start background worker only
#   scheduler - Start scheduler only
#   all       - Start all services (default)
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Configuration
APP_NAME="B-Snap"
APP_DIR="/app"
LOG_DIR="${APP_DIR}/logs"
STATIC_DIR="${APP_DIR}/static"
MODE="${1:-web}"

# Create necessary directories
create_directories() {
    log_info "Creating necessary directories..."
    mkdir -p "${LOG_DIR}"
    mkdir -p "${STATIC_DIR}/snapshots"
    mkdir -p "${STATIC_DIR}/videos"
    mkdir -p "${STATIC_DIR}/images"
    log_success "Directories created"
}

# Wait for database to be ready
wait_for_db() {
    if [[ -n "${DATABASE_URL:-}" ]]; then
        log_info "Waiting for database..."
        
        local max_attempts=30
        local attempt=1
        
        while [ $attempt -le $max_attempts ]; do
            if python3 << EOF 2>/dev/null
import sys
from sqlalchemy import create_engine, text
try:
    engine = create_engine('$DATABASE_URL', connect_args={'connect_timeout': 5})
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
        conn.commit()
    sys.exit(0)
except Exception as e:
    sys.exit(1)
EOF
            then
                log_success "Database is ready"
                return 0
            fi
            
            log_warning "Database not ready, attempt $attempt/$max_attempts. Retrying in 2s..."
            sleep 2
            attempt=$((attempt + 1))
        done
        
        log_error "Database connection failed after $max_attempts attempts"
        return 1
    fi
}

# Run database migrations
run_migrations() {
    log_info "Running database migrations..."
    cd "${APP_DIR}"
    
    if alembic upgrade head; then
        log_success "Migrations completed"
    else
        log_warning "Migration failed, attempting to initialize database..."
        python3 -c "
from app.db.database import Base, engine
Base.metadata.create_all(bind=engine)
print('Database tables created')
"
        # Stamp alembic version
        alembic stamp head || true
    fi
}

# Collect static files (if needed)
collect_static() {
    log_info "Setting up static files..."
    # Ensure CSS output exists
    if [ ! -f "${STATIC_DIR}/css/output.css" ]; then
        log_warning "Tailwind CSS output not found. Building..."
        if command -v npm &> /dev/null && [ -f "${APP_DIR}/package.json" ]; then
            cd "${APP_DIR}"
            npm run build
            log_success "Tailwind CSS built"
        else
            log_warning "npm not available or package.json not found. Using default CSS."
        fi
    fi
}

# Check environment
check_environment() {
    log_info "Checking environment..."
    
    # Check Python
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed"
        exit 1
    fi
    
    # Check critical environment variables
    if [[ -z "${SECRET_KEY:-}" ]]; then
        log_error "SECRET_KEY environment variable is not set"
        exit 1
    fi
    
    if [[ "${SECRET_KEY}" == "your-default-secret-key-for-dev" || "${SECRET_KEY}" == "change-this-in-production" ]]; then
        log_warning "Using default SECRET_KEY. This is insecure for production!"
    fi
    
    # Check database URL
    if [[ -z "${DATABASE_URL:-}" ]]; then
        log_warning "DATABASE_URL not set, using SQLite"
    else
        # Show masked DATABASE_URL for debugging
        local masked_url
        masked_url=$(echo "$DATABASE_URL" | sed -E 's/(:\/\/[^:]+:)[^@]+(@)/\1***\2/')
        log_info "DATABASE_URL: $masked_url"
    fi
    
    log_success "Environment check passed"
}

# Start web server
start_web() {
    log_info "Starting ${APP_NAME} web server..."
    
    local workers="${WORKERS:-2}"
    local bind="${BIND:-0.0.0.0:8080}"
    local timeout="${TIMEOUT:-60}"
    local keepalive="${KEEPALIVE:-2}"
    
    log_info "Configuration:"
    log_info "  Workers: $workers"
    log_info "  Bind: $bind"
    log_info "  Timeout: $timeout"
    log_info "  Keepalive: $keepalive"
    
    # Check if gunicorn config exists
    local gunicorn_opts=""
    if [ -f "${APP_DIR}/gunicorn.conf.py" ]; then
        gunicorn_opts="-c ${APP_DIR}/gunicorn.conf.py"
        log_info "Using gunicorn.conf.py"
    else
        gunicorn_opts="--workers $workers --bind $bind --timeout $timeout --keep-alive $keepalive"
        gunicorn_opts="$gunicorn_opts -k uvicorn.workers.UvicornWorker"
    fi
    
    cd "${APP_DIR}"
    exec gunicorn app.main:app $gunicorn_opts
}

# Start scheduler
start_scheduler() {
    log_info "Starting ${APP_NAME} scheduler..."
    cd "${APP_DIR}"
    exec python3 -m app.jobs.scheduler_main
}

# Start notifier
start_notifier() {
    log_info "Starting ${APP_NAME} notifier..."
    cd "${APP_DIR}"
    exec python3 -m app.ws.notifier
}

# Print usage
print_usage() {
    cat << EOF
Usage: $(basename "$0") [MODE]

Modes:
  web       Start web server only (default)
  scheduler Start scheduler service only
  notifier  Start notifier service only
  migrate   Run database migrations only
  check     Run environment checks only
  help      Show this help message

Environment Variables:
  DATABASE_URL      Database connection URL
  SECRET_KEY        Secret key for encryption
  WORKERS           Number of gunicorn workers (default: 2)
  BIND              Bind address (default: 0.0.0.0:8080)
  TIMEOUT           Worker timeout (default: 60)
  LOG_LEVEL         Logging level (default: INFO)
  TZ                Timezone (default: UTC)

Examples:
  $(basename "$0")              # Start web server
  $(basename "$0") web          # Start web server
  $(basename "$0") scheduler    # Start scheduler
  $(basename "$0") migrate      # Run migrations only
EOF
}

# Main function
main() {
    log_info "Starting ${APP_NAME}..."
    log_info "Mode: ${MODE}"
    
    case "${MODE}" in
        web)
            check_environment
            create_directories
            wait_for_db
            run_migrations
            collect_static
            start_web
            ;;
        scheduler)
            check_environment
            create_directories
            wait_for_db
            start_scheduler
            ;;
        notifier)
            check_environment
            create_directories
            start_notifier
            ;;
        all)
            log_error "Mode 'all' not yet implemented. Use docker-compose for multi-service setup."
            exit 1
            ;;
        migrate)
            wait_for_db
            run_migrations
            ;;
        check)
            check_environment
            log_success "All checks passed!"
            ;;
        help|--help|-h)
            print_usage
            exit 0
            ;;
        *)
            log_error "Unknown mode: ${MODE}"
            print_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
