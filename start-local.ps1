# =============================================================================
# B-Snap Local Startup Script (Windows)
# =============================================================================
# This script starts the B-Snap application for local development without Docker.
#
# Usage:
#   .\start-local.ps1 [web|worker|scheduler|notifier|migrate|check]
#
# Modes:
#   web       - Start web server only (default)
#   scheduler - Start scheduler service only
#   notifier  - Start notifier service only
#   migrate   - Run database migrations only
#   check     - Run environment checks only
# =============================================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet("web", "dev", "scheduler", "notifier", "migrate", "check", "help")]
    [string]$Mode = "web"
)

# Colors for output
function Write-Info { param($Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Success { param($Message) Write-Host "[SUCCESS] $Message" -ForegroundColor Green }
function Write-Warning { param($Message) Write-Host "[WARNING] $Message" -ForegroundColor Yellow }
function Write-Error { param($Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

# Get project root
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# Configuration
$AppName = "B-Snap"
$LogDir = Join-Path $ProjectRoot "logs"
$StaticDir = Join-Path $ProjectRoot "static"

# Load environment variables from .env file
function Load-EnvFile {
    if (Test-Path ".env") {
        Write-Info "Loading environment from .env file..."
        Get-Content ".env" | ForEach-Object {
            if ($_ -match '^\s*([^#][^=]+)\s*=\s*(.*)\s*$') {
                $name = $matches[1].Trim()
                $value = $matches[2].Trim()
                # Remove quotes if present
                if ($value -match '^["'']') {
                    $value = $value -replace '^["'']|["'']$'
                }
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
        Write-Success "Environment loaded"
    } else {
        Write-Warning ".env file not found. Using default environment variables."
    }
}

# Create necessary directories
function Create-Directories {
    Write-Info "Creating necessary directories..."
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StaticDir "snapshots") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StaticDir "videos") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $StaticDir "images") | Out-Null
    Write-Success "Directories created"
}

# Wait for database to be ready
function Wait-ForDatabase {
    $dbUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
    if (-not $dbUrl) {
        Write-Warning "DATABASE_URL not set, using SQLite (no connection check needed)"
        return $true
    }

    Write-Info "Waiting for database..."
    $maxAttempts = 30
    $attempt = 1

    while ($attempt -le $maxAttempts) {
        try {
            $pythonCode = @"
import sys
from sqlalchemy import create_engine, text
try:
    engine = create_engine('$dbUrl', connect_args={'connect_timeout': 5})
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
        conn.commit()
    sys.exit(0)
except Exception as e:
    sys.exit(1)
"@
            $result = python -c $pythonCode 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Success "Database is ready"
                return $true
            }
        } catch {
            # Ignore errors
        }

        Write-Warning "Database not ready, attempt $attempt/$maxAttempts. Retrying in 2s..."
        Start-Sleep -Seconds 2
        $attempt++
    }

    Write-Error "Database connection failed after $maxAttempts attempts"
    return $false
}

# Run database migrations
function Run-Migrations {
    Write-Info "Running database migrations..."
    
    # First, ensure all tables exist by creating them from models
    Write-Info "Ensuring database tables exist..."
    python -c "from app.db.database import Base, engine; Base.metadata.create_all(bind=engine); print('Database tables created/updated')"
    
    # Check if alembic_version table exists
    $dbUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
    if ($dbUrl) {
        $checkVersionTable = python -c "
import sys
from sqlalchemy import create_engine, text
try:
    engine = create_engine('$dbUrl')
    with engine.connect() as conn:
        result = conn.execute(text(\"SELECT 1 FROM alembic_version\"))
        sys.exit(0)
except:
    sys.exit(1)
" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Info "Alembic version table not found, stamping current version..."
            alembic stamp head 2>$null
        }
    }
    
    # Then run alembic migrations
    try {
        alembic upgrade head
        Write-Success "Migrations completed"
    } catch {
        Write-Warning "Migration upgrade had issues, attempting to stamp current version..."
        alembic stamp head 2>$null
        Write-Success "Version stamped"
    }
}

# Check environment
function Check-Environment {
    Write-Info "Checking environment..."

    # Check Python
    try {
        $pythonVersion = python --version 2>&1
        Write-Success "Python found: $pythonVersion"
    } catch {
        Write-Error "Python is not installed or not in PATH"
        exit 1
    }

    # Check FFmpeg
    try {
        $ffmpegPath = (Get-Command ffmpeg -ErrorAction Stop).Source
        if (Test-Path "$ProjectRoot\ffmpeg.exe") {
            $ffmpegVersion = & "$ProjectRoot\ffmpeg.exe" -version 2>&1 | Select-Object -First 1
            Write-Success "FFmpeg found: $ffmpegVersion"
        } else {
            $ffmpegVersion = ffmpeg -version 2>&1 | Select-Object -First 1
            Write-Success "FFmpeg found: $ffmpegVersion"
        }
    } catch {
        if (Test-Path "$ProjectRoot\ffmpeg.exe") {
            Write-Success "FFmpeg found in project root"
        } else {
            Write-Warning "FFmpeg not found in PATH. Install FFmpeg or add it to PATH."
        }
    }

    # Check critical environment variables
    $secretKey = [Environment]::GetEnvironmentVariable("SECRET_KEY", "Process")
    if (-not $secretKey) {
        Write-Error "SECRET_KEY environment variable is not set"
        exit 1
    }

    if ($secretKey -eq "your-very-secret-key-change-this-in-production" -or
        $secretKey -eq "change-this-in-production") {
        Write-Warning "Using default SECRET_KEY. This is insecure for production!"
    }

    # Check database URL
    $dbUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
    if (-not $dbUrl) {
        Write-Warning "DATABASE_URL not set, will use SQLite"
    } else {
        $maskedUrl = $dbUrl -replace '(://[^:]+:)[^@]+(@)', '$1***$2'
        Write-Info "DATABASE_URL: $maskedUrl"
    }

    Write-Success "Environment check passed"
}

# Start web server
function Start-WebServer {
    Write-Info "Starting $AppName web server..."

    $port = [Environment]::GetEnvironmentVariable("PORT", "Process")
    if (-not $port) { $port = "8080" }

    $hostAddr = "127.0.0.1"

    Write-Info "Configuration:"
    Write-Info "  Host: $hostAddr"
    Write-Info "  Port: $port"
    Write-Info "  Workers: Using uvicorn defaults (Gunicorn not supported on Windows)"

    Write-Info "Starting uvicorn server..."
    # Using uvicorn directly - gunicorn not supported on Windows
    & uvicorn app.main:app --host $hostAddr --port $port
}

# Start development server (uvicorn with reload)
function Start-DevServer {
    Write-Info "Starting $AppName development server..."
    Write-Info "Configuration:"
    Write-Info "  Host: 127.0.0.1"
    Write-Info "  Port: 8000"
    Write-Info "  Reload: enabled"

    & uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
}

# Start scheduler
function Start-Scheduler {
    Write-Info "Starting $AppName scheduler..."
    & python -m app.jobs.scheduler_main
}

# Start notifier
function Start-Notifier {
    Write-Info "Starting $AppName notifier..."
    & python -m app.ws.notifier
}

# Print usage
function Print-Usage {
    Write-Host @"
Usage: .\start-local.ps1 [MODE]

Modes:
  web       Start web server with uvicorn (default)
  dev       Start development server with auto-reload
  scheduler Start scheduler service only
  notifier  Start notifier service only
  migrate   Run database migrations only
  check     Run environment checks only
  help      Show this help message

Environment Variables:
  DATABASE_URL      Database connection URL (optional, defaults to SQLite)
  SECRET_KEY        Secret key for encryption (required)
  WORKERS           Number of gunicorn workers (default: 2)
  PORT              Server port (default: 8080)
  BIND              Bind address (default: 127.0.0.1:8080)
  TIMEOUT           Worker timeout (default: 60)
  LOG_LEVEL         Logging level (default: INFO)

Examples:
  .\start-local.ps1           # Start web server
  .\start-local.ps1 web       # Start web server
  .\start-local.ps1 dev       # Start development server
  .\start-local.ps1 scheduler # Start scheduler
  .\start-local.ps1 migrate   # Run migrations only
"@
}

# Main execution
function Main {
    if ($Mode -eq "help") {
        Print-Usage
        exit 0
    }

    Write-Info "Starting $AppName..."
    Write-Info "Mode: $Mode"
    Write-Info "Project Root: $ProjectRoot"

    Load-EnvFile

    switch ($Mode) {
        "web" {
            Check-Environment
            Create-Directories
            Wait-ForDatabase | Out-Null
            Run-Migrations
            Start-WebServer
        }
        "dev" {
            Check-Environment
            Create-Directories
            Wait-ForDatabase | Out-Null
            Run-Migrations
            Start-DevServer
        }
        "scheduler" {
            Check-Environment
            Create-Directories
            Wait-ForDatabase | Out-Null
            Start-Scheduler
        }
        "notifier" {
            Check-Environment
            Create-Directories
            Start-Notifier
        }
        "migrate" {
            Wait-ForDatabase | Out-Null
            Run-Migrations
        }
        "check" {
            Check-Environment
            Write-Success "All checks passed!"
        }
        default {
            Write-Error "Unknown mode: $Mode"
            Print-Usage
            exit 1
        }
    }
}

# Run main function
Main
