@echo off
REM =============================================================================
REM B-Snap Local Startup Script (Windows CMD)
REM =============================================================================
REM Usage: start-local.bat [web|scheduler|notifier|migrate|check]
REM =============================================================================

setlocal enabledelayedexpansion

set "MODE=%~1"
if "%MODE%"=="" set "MODE=web"

echo [INFO] Starting B-Snap in %MODE% mode...
echo [INFO] Project Root: %CD%

REM Load environment from .env file
if exist ".env" (
    echo [INFO] Loading environment from .env file...
    for /f "usebackq tokens=*" %%a in (".env") do (
        for /f "tokens=1,* delims==" %%b in ("%%a") do (
            set "line=%%a"
            if not "!line:~0,1!"=="#" (
                if not "%%c"=="" (
                    set "val=%%c"
                    set "val=!val:"=!"
                    set "val=!val:'=!"
                    set "%%b=!val!"
                )
            )
        )
    )
    echo [SUCCESS] Environment loaded
) else (
    echo [WARNING] .env file not found
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    exit /b 1
)
echo [SUCCESS] Python found

REM Check SECRET_KEY
if "%SECRET_KEY%"=="" (
    echo [ERROR] SECRET_KEY environment variable is not set
    exit /b 1
)

REM Create directories
if not exist "logs" mkdir logs
if not exist "static\snapshots" mkdir static\snapshots
if not exist "static\videos" mkdir static\videos
if not exist "static\images" mkdir static\images
echo [SUCCESS] Directories created

REM Execute based on mode
if "%MODE%"=="web" goto :web
if "%MODE%"=="dev" goto :dev
if "%MODE%"=="scheduler" goto :scheduler
if "%MODE%"=="notifier" goto :notifier
if "%MODE%"=="migrate" goto :migrate
if "%MODE%"=="check" goto :check
echo [ERROR] Unknown mode: %MODE%
goto :usage

:web
echo [INFO] Starting web server...
python -c "from app.db.database import Base, engine; Base.metadata.create_all(bind=engine); print('Database tables created/updated')"
alembic upgrade head
echo [INFO] Starting uvicorn server...
uvicorn app.main:app --host 127.0.0.1 --port 8080
goto :end

:dev
echo [INFO] Starting development server...
python -c "from app.db.database import Base, engine; Base.metadata.create_all(bind=engine); print('Database tables created/updated')"
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
goto :end

:scheduler
echo [INFO] Starting scheduler...
python -m app.jobs.scheduler_main
goto :end

:notifier
echo [INFO] Starting notifier...
python -m app.ws.notifier
goto :end

:migrate
echo [INFO] Running migrations...
alembic upgrade head
goto :end

:check
echo [INFO] Environment check passed!
goto :end

:usage
echo.
echo Usage: start-local.bat [MODE]
echo.
echo Modes:
echo   web       Start web server with gunicorn (default)
echo   dev       Start development server with auto-reload
echo   scheduler Start scheduler service
echo   notifier  Start notifier service
echo   migrate   Run database migrations
echo   check     Check environment

:end
endlocal
