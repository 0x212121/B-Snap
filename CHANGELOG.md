# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Orphaned Files Management** (`/orphaned-files`) - Complete solution for managing orphaned snapshot files:
  - **Folder Scanner** - Detects snapshot files on disk with no database record
  - **OrphanedFile Table** - Tracks orphaned files with metadata (path, size, camera_id, status)
  - **Dual Detection Methods**:
    - **Database-triggered**: When camera deleted, snapshots marked `is_orphaned=true`
    - **Disk Scanner**: Scheduled job scans folder vs DB records daily at 3 AM
  - **Management UI**:
    - **Storage Summary**: Total files, size in MB, breakdown by camera
    - **File List**: View all orphaned files with status filters (pending/reviewed/deleted)
    - **Scan Button**: Manual trigger to scan disk and sync to database
    - **Dry Run**: Preview cleanup without deleting files
    - **Cleanup All**: Bulk delete pending files with confirmation
    - **Force Cleanup**: Direct disk cleanup bypassing table check
    - **Review Function**: Mark files as reviewed for audit trail
  - **Auto Cleanup**: Empty `camera_id/<date>/` folders removed after file deletion
  - **API Endpoints**: `/api/orphaned-files/*` for scan, cleanup, summary, review
  - **Menu Navigation**: Added "🧹 Orphaned Files" to Administration menu
  - **Database Migrations**: 
    - `20260310_orphaned_snapshots` - Added `is_orphaned` column to snapshots
    - `20260311_add_orphaned_files` - New `orphaned_files` table
    - `20260311_fix_snapshot_fk_cascade` - Changed FK from CASCADE to SET NULL

### Fixed
- **Job Management API** - Fixed missing `HTTPException` import in `app/routes/jobs.py` that caused API errors
- **Job Stats JSON Serialization** - Fixed `Decimal` type not JSON serializable error in `/api/jobs/stats` endpoint by converting SQLAlchemy Decimal values to Python int/float
- **SQLAlchemy Case Syntax** - Updated `case()` function calls in `JobExecutionLog.get_job_stats()` to use SQLAlchemy 2.0 syntax (positional arguments instead of list)
- **CSV Upload Camera** - Fixed camera not being created when uploading CSV file. Previously only counted success but never actually created the camera records in database. Now properly creates cameras with all fields (hostname, ip, port, username, password, location, group, status, coordinates)
- **Camera Locations API** - Fixed `/camera-locations` endpoint error 500 by making schema fields optional (ip, user_group, coordinate) to handle cameras with NULL values

### Changed
- **Job Schedule Modal** - Replaced browser native `prompt()` popup with modern Tailwind-styled modal:
  - **Modern Design** - Backdrop blur, rounded corners, dark mode support
  - **Input Field** - Styled text input with monospace font for cron expression
  - **Examples Panel** - Visual guide with common cron patterns in indigo-themed box
  - **Keyboard Shortcuts** - Enter to save, Escape to close
  - **Consistent UI** - Matches other modals (history modal) in the application

# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.13.0] - 2026-03-09

### Added

#### Job Management & Scheduling
- **Job Management Dashboard** (`/admin/jobs`) for monitoring and controlling scheduled background jobs.
- **Job execution tracking** using `JobExecutionLog` with history, duration, and status logging.
- **Cron-based scheduling system** supporting dynamic cron updates via configuration.
- **Persistent job storage** using `SQLAlchemyJobStore` (`apscheduler_jobs` table).
- **Settings UI section for Job Scheduling** with cron configuration and interval fallbacks.

#### Reporting & SLA Monitoring
- **Executive PDF Report Generator** (`POST /health/history/report`)
  - Professional health reports with executive summary
  - PDF, Excel, and CSV export formats
  - Charts and device uptime summaries
- **SLA & Compliance Dashboard** (`/health/sla-report`)
  - SLA compliance monitoring (default 99.5%)
  - MTTR and MTBF metrics
  - Incident severity classification (Critical / Major / Minor)
  - Performance highlights and compliance indicators
- **Scheduled Reports API**
  - Automated report generation (daily, weekly, monthly)
  - Email delivery to multiple recipients
  - New models: `SLAReport`, `ScheduledReport`

#### Notifications
- **Real-time Toast Notification System**
  - Success, error, warning, and info notifications
  - WebSocket-based delivery
  - Dark mode support
  - Action buttons and persistent notifications
  - Demo page available at `/demo/toast/`

#### WhatsApp Integration
- **GoWA WhatsApp Gateway support**
  - WhatsApp notifications and scheduled system reports
  - Daily camera status report and storage alerts
  - WhatsApp bot commands: `help`, `status`, `cameras`, `health`, `snapshot`, `report`
  - Services: `WAGatewayService`, `WABotHandler`

#### UI / UX Improvements
- **Audit Logs page redesign** (`/audit-logs`)
  - Stats cards, advanced filtering, and CSV export
  - Improved pagination and JSON detail viewer
- **WhatsApp Whitelist page improvements** (`/admin/whitelist`)
  - Stats overview, role badges, toggle switches, modal forms
- **Email Logs dashboard improvements** (`/email-logs`)
  - Status metrics, filtering, CSV export, and error details modal
- **Logs Viewer redesign**
  - Smart log parsing, live filtering, log statistics, and auto-refresh
- **Recipients UI improvements**
  - More compact layout and confirmation modal for test email sending

### Fixed

- Fixed **scheduler restart conflict (`ConflictingIdError`)** when jobs already exist in database.
- Fixed **migration error** when `apscheduler_jobs` table already exists.
- Fixed **Snapshot Gallery 500 errors** caused by incorrect UUID-to-int conversion.
- Fixed **toast notification JavaScript scope issues** for global handlers.
- Fixed **audit logs pagination click handlers**.
- Fixed **email statistics counting failed emails as sent**.
- Fixed **missing logger registrations** that prevented logs from being written correctly.

### Changed

- Replaced legacy notification system with **real-time toast notifications**.
- **Logs viewer interface redesigned** with improved filtering and navigation.
- **Recipients UI redesigned** with compact layout and confirmation modal.

## [1.12.0] - 2026-03-06

### Added
- **Storage Monitor Dashboard** - Complete storage monitoring and alerting system:
  - **Storage Health Widget** on Stats page (`/stats`) showing:
    - Real-time disk usage with visual progress bar
    - Color-coded alerts (green/yellow/red) based on thresholds
    - Total / Used / Free space display
    - Daily growth rate tracking (GB/day)
    - Days-until-full prediction
  - **Usage Breakdown by Category**:
    - Snapshots folder size
    - Videos folder size  
    - Logs folder size
    - Other files
  - **Configurable Thresholds** (in `/config`):
    - Critical: 95% or < 5GB free (default)
    - Warning: 85% (default)
    - Info: 75% (default)
    - Critical Free Space: 5GB (default)
  - **Automatic Monitoring**:
    - Scheduler runs every 1 hour
    - Records metrics to database
    - Calculates growth trend from historical data
  - **Manual Check**:
    - "Refresh" button on widget
    - API endpoint: `POST /storage/check-now`
    - With audit logging
  - **Alert History**:
    - Tracks critical/warning alerts
    - Mark alerts as resolved
    - API: `GET /storage/alerts`
  - **Trend Analysis**:
    - 7-day historical data
    - API: `GET /storage/trend?days=7`
  - **Docker Support**:
    - Compatible with container paths: `/static/snapshots`, `/static/videos`, `/logs`
    - Fallback to local paths for development
  - **New Database Tables**:
    - `storage_metrics` - Historical usage data
    - `storage_alerts` - Alert history
  - **Files Added/Modified**:
    - `app/models/storage_metric.py` - New models
    - `app/utils/storage_monitor.py` - Core monitoring logic
    - `app/routes/stats.py` - API endpoints
    - `templates/stats.html` - Widget UI
    - `templates/config.html` - Configuration UI
    - `templates/_icons.html` - Icons
    - `app/jobs/scheduler.py` - Scheduled job
    - `app/core/config_initializer.py` - Default configs
    - `alembic/versions/20260306_add_storage_monitoring.py` - Migration

- **Log Retention Settings Dashboard** - Manage cleanup policies in `/config`:
  - Retention periods (7-3650 days):
    - Audit Logs: 180 days (default)
    - API Logs: 90 days (default)
    - Command Logs: 90 days (default)
    - Camera Stats: 90 days (default)
    - Email Logs: 90 days (default)
  - "Run Cleanup Now" button with **password verification**
  - **Audit logging** for all cleanup attempts
  - Dynamic configuration (no restart required)

### Fixed
- **Scheduler Thread Pool Initialization** - Fixed lazy initialization to prevent database connection errors during gunicorn boot
- **Config Page Variable Conflict** - Fixed variable name collision in config save
- **Database Models Import** - Fixed undefined `models` reference in `database.py`
- **Template Icon Import** - Added missing `icon_database` import in config.html
- **Validation Logic** - Separated storage threshold validation from retention validation
- **Migration Safety** - Added table existence checks to prevent duplicate creation errors

### Security
- Added password verification for destructive operations (log cleanup)
- Audit logging for all manual storage checks and cleanup operations

---

## [1.11.0] - 2025-10-24

### Added
- Watermark support for recorded videos.
- Asset Number and Camera Coordinates included in email alerts.
- `reason` column added to email logs for improved traceability.
- Admins can now generate Executive Reports from the Insights Dashboard.

## [1.10.2] - 2025-10-16

### Added
- Standalone (local) camera to map.

### Fixed
- Pagination bug in email recipients menu.

## [1.10.1] - 2025-10-10
### Changed
- Updated documentation and fix minor bugs. 

## [1.10.0] - 2025-10-09
### Added
- Usage Insight Dashboard — new analytics page to visualize daily API activity and chatbot command usage. Includes interactive line and bar charts, summary cards, and top-10 command statistics for improved insight into system activity.

### Changed
- Minor UI adjustments and layout refinements across several pages for better visual consistency.

## [1.9.1] - 2025-10-08
### Fixed
- Email notification send to wrong group.

## [1.9.0] - 2025-10-08
### Added
- Email notification alert (use env to setup SMTP variable) and email sending logs via UI.
- Email recipient list endpoint.
- Custom 403, 404, and 500 page.

### Changed
- Redesign icon and layout.
