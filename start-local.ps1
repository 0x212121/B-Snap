# =============================================================================
# B-Snap Local Startup Script (Windows)
# =============================================================================
# This script starts the B-Snap application for local development without Docker.
#
# Usage:
#   .\start-local.ps1 [web|dev|scheduler|notifier|migrate|check|all]
#
# Modes:
#   web       - Start web server only (default)
#   dev       - Start development server with auto-reload
#   scheduler - Start scheduler service only
#   notifier  - Start notifier service only
#   all       - Start all services (web + scheduler + notifier)
#   migrate   - Run database migrations only
#   check     - Run environment checks only
# =============================================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet("web", "dev", "scheduler", "notifier", "migrate", "check", "all", "help")]
    [string]$Mode = "web"
)

# Global variable to track child processes for cleanup
$script:ChildProcesses = @()

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

    python -m app.db.migrate
    if ($LASTEXITCODE -ne 0) {
        throw "Database migrations or configuration initialization failed."
    }
    Write-Success "Migrations and configuration initialization completed"
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

# Resolve the Uvicorn bind address. BIND uses HOST:PORT format, e.g.
# 0.0.0.0:8080. PORT, when set, overrides the port portion.
function Get-WebServerSettings {
    param(
        [string]$DefaultHost = "0.0.0.0",
        [int]$DefaultPort = 8080,
        [switch]$UseBindPort
    )

    $bindValue = [Environment]::GetEnvironmentVariable("BIND", "Process")
    $hostAddr = $DefaultHost
    $port = $DefaultPort

    if ($bindValue) {
        if ($bindValue -match '^\[(?<host>[^\]]+)\]:(?<port>\d+)$') {
            $hostAddr = $matches.host
            if ($UseBindPort) { $port = [int]$matches.port }
        } elseif ($bindValue -match '^(?<host>[^:]+):(?<port>\d+)$') {
            $hostAddr = $matches.host
            if ($UseBindPort) { $port = [int]$matches.port }
        } else {
            throw "Invalid BIND '$bindValue'. Expected HOST:PORT, for example 0.0.0.0:8080."
        }
    }

    $portOverride = [Environment]::GetEnvironmentVariable("PORT", "Process")
    if ($portOverride) { $port = [int]$portOverride }

    return @{ Host = $hostAddr; Port = $port }
}

# Start web server
function Start-WebServer {
    Write-Info "Starting $AppName web server..."

    $settings = Get-WebServerSettings -DefaultHost "0.0.0.0" -DefaultPort 8080 -UseBindPort
    $hostAddr = $settings.Host
    $port = $settings.Port

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
    $settings = Get-WebServerSettings -DefaultHost "127.0.0.1" -DefaultPort 8000
    Write-Info "Configuration:"
    Write-Info "  Host: $($settings.Host)"
    Write-Info "  Port: $($settings.Port)"
    Write-Info "  Reload: enabled"

    & uvicorn app.main:app --reload --host $settings.Host --port $settings.Port
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

# Start all services
function Start-AllServices {
    Write-Info "Starting all $AppName services..."
    
    # Create log files for each service
    $timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
    $schedulerLog = Join-Path $LogDir "scheduler_$timestamp.log"
    $schedulerErrLog = Join-Path $LogDir "scheduler_$timestamp.error.log"
    $notifierLog = Join-Path $LogDir "notifier_$timestamp.log"
    $notifierErrLog = Join-Path $LogDir "notifier_$timestamp.error.log"
    
    Write-Info "Service logs will be written to:"
    Write-Info "  Scheduler: $schedulerLog (errors: $schedulerErrLog)"
    Write-Info "  Notifier:  $notifierLog (errors: $notifierErrLog)"
    Write-Info ""
    
    # Start scheduler in background
    Write-Info "Starting scheduler service in background..."
    $schedulerProcess = Start-Process -FilePath "python" -ArgumentList "-m", "app.jobs.scheduler_main" -RedirectStandardOutput $schedulerLog -RedirectStandardError $schedulerErrLog -PassThru -WindowStyle Hidden
    $script:ChildProcesses += $schedulerProcess
    Write-Success "Scheduler started (PID: $($schedulerProcess.Id))"
    
    # Give scheduler a moment to initialize
    Start-Sleep -Seconds 3
    
    # Check if scheduler is still running
    if ($schedulerProcess.HasExited) {
        Write-Error "Scheduler failed to start. Check logs:"
        Write-Error "  Output: $schedulerLog"
        Write-Error "  Errors: $schedulerErrLog"
        Stop-AllServices
        exit 1
    }
    
    # Start notifier in background
    Write-Info "Starting notifier service in background..."
    $notifierProcess = Start-Process -FilePath "python" -ArgumentList "-m", "app.ws.notifier" -RedirectStandardOutput $notifierLog -RedirectStandardError $notifierErrLog -PassThru -WindowStyle Hidden
    $script:ChildProcesses += $notifierProcess
    Write-Success "Notifier started (PID: $($notifierProcess.Id))"
    
    # Give notifier a moment to initialize
    Start-Sleep -Seconds 2
    
    # Check if notifier is still running
    if ($notifierProcess.HasExited) {
        Write-Error "Notifier failed to start. Check logs:"
        Write-Error "  Output: $notifierLog"
        Write-Error "  Errors: $notifierErrLog"
        Stop-AllServices
        exit 1
    }
    
    Write-Info ""
    Write-Success "All background services started successfully!"
    Write-Info ""
    Write-Info "Starting web server (press Ctrl+C to stop all services)..."
    Write-Info ""
    
    # Start web server in foreground
    try {
        $settings = Get-WebServerSettings -DefaultHost "0.0.0.0" -DefaultPort 8080 -UseBindPort
        $hostAddr = $settings.Host
        $port = $settings.Port
        
        # Use Start-Process with -NoNewWindow so output appears in current console
        $webProcess = Start-Process -FilePath "uvicorn" -ArgumentList "app.main:app", "--host", $hostAddr, "--port", $port -NoNewWindow -PassThru
        $script:ChildProcesses += $webProcess
        
        # Wait for web process to exit
        $webProcess.WaitForExit()
    }
    catch {
        Write-Error "Web server encountered an error: $_"
    }
    finally {
        Stop-AllServices
    }
}

# Stop all background services
function Stop-AllServices {
    Write-Info ""
    Write-Info "Stopping all services..."
    
    foreach ($process in $script:ChildProcesses) {
        if ($process -and -not $process.HasExited) {
            try {
                Write-Info "Stopping process $($process.Id)..."
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
            catch {
                # Process may have already exited
            }
        }
    }
    
    # Also try to find and stop any remaining Python processes for our modules
    Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -match "app\.jobs\.scheduler_main|app\.ws\.notifier"
    } | ForEach-Object {
        try {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
        catch {}
    }
    
    Write-Success "All services stopped"
}

# Handle Ctrl+C / interrupt
function Handle-Interrupt {
    Write-Info ""
    Write-Warning "Interrupted by user"
    Stop-AllServices
    exit 0
}

# Register Ctrl+C handler
[Console]::TreatControlCAsInput = $false

# Print usage
function Print-Usage {
    Write-Host @"
Usage: .\start-local.ps1 [MODE]

Modes:
  web       Start web server with uvicorn (default)
  dev       Start development server with auto-reload
  scheduler Start scheduler service only
  notifier  Start notifier service only
  all       Start all services (web + scheduler + notifier)
  migrate   Run database migrations only
  check     Run environment checks only
  help      Show this help message

Environment Variables:
  DATABASE_URL      Database connection URL (optional, defaults to SQLite)
  SECRET_KEY        Secret key for encryption (required)
  WORKERS           Number of gunicorn workers (default: 2)
  PORT              Server port (default: 8080)
  BIND              Bind address as HOST:PORT (default: 0.0.0.0:8080 for web/all)
  TIMEOUT           Worker timeout (default: 60)
  LOG_LEVEL         Logging level (default: INFO)

Examples:
  .\start-local.ps1           # Start web server
  .\start-local.ps1           # Start web server only
  .\start-local.ps1 all       # Start all services (web + scheduler + notifier)
  .\start-local.ps1 web       # Start web server only
  .\start-local.ps1 dev       # Start development server with auto-reload
  .\start-local.ps1 scheduler # Start scheduler only
  .\start-local.ps1 notifier  # Start notifier only
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
        "all" {
            Check-Environment
            Create-Directories
            Wait-ForDatabase | Out-Null
            Run-Migrations
            
            # Set up interrupt handler
            $null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
                Stop-AllServices
            }
            
            Start-AllServices
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
