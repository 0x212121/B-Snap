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

## [1.14.0] - 2026-03-09
### Added
- **Executive PDF Report Generator** (`POST /health/history/report`) - Professional health reports for management and compliance:
  - **Cover Page** with logo and report period
  - **Executive Summary** with total uptime, biggest incidents, SLA compliance
  - **Charts Support** - Embed uptime trend and outage heatmap charts
  - **Device Details Table** with sorting and filtering (top 50 in PDF)
  - **SLA Compliance Summary** with Pass/Fail/Warning status
  - **Multiple Formats** - PDF, Excel, CSV export options
  - **Quick Export** - CSV/Excel export from health history page (`/health/history/export`)
  
- **SLA & Compliance Dashboard** (`/health/sla-report`) - Enterprise-grade SLA monitoring:
  - **SLA Compliance Report** - Per-device compliance tracking with configurable threshold (default: 99.5%)
  - **MTTR Calculation** - Mean Time To Recovery in seconds
  - **MTBF Calculation** - Mean Time Between Failures in seconds
  - **Incident Severity Classification** - Critical (≥1h), Major (10m-1h), Minor (<10m)
  - **Compliance Status** - Pass/Fail/Warning indicators with visual badges
  - **Performance Highlights** - Best and worst performer identification
  - **Summary Statistics** - Total cameras, average uptime, incident counts
  - **Print-friendly** - Optimized layout for printed reports
  
- **Scheduled Reports** (API) - Automated report generation:
  - **New Models**: `SLAReport` and `ScheduledReport`
  - **Schedule Configuration** - Daily, weekly, monthly frequency
  - **Report Parameters** - Lookback days, SLA threshold, format selection
  - **Recipient Management** - Multiple email recipients support
  - **API Endpoints** - List, create, delete scheduled reports
  - **Database Migration**: `20260309_add_sla_reports`

## [1.13.0] - 2026-03-09
### Added
- **Job Management Dashboard** (`/admin/jobs`) - Complete job scheduling and monitoring system:
  - **4 Stats Cards**: Total Runs (24h), Success Rate, Failed Jobs, Active Jobs
  - **Jobs Table**: Schedule, Last Run, Next Run, 24h Statistics
  - **Manual Job Execution**: Run now button with immediate scheduling
  - **Job History Modal**: View last 50 executions with duration and status
  - **Auto-refresh**: Toggle-able 30-second auto-refresh
  - **Cron Schedule Display**: Visual badge showing current cron expression
  - **Quick Schedule Edit**: Edit cron directly from dashboard
  
- **Job Execution Tracking** - Database-backed job execution logging:
  - **New Model**: `JobExecutionLog` with timing, status, error details
  - **Automatic Logging**: All jobs wrapped with execution decorator
  - **Statistics**: Per-job and overall statistics (success rate, total runs)
  - **History API**: Query execution history with filters
  - **Log Retention**: Configurable retention period (default: 30 days)
  
- **Cron Scheduling** - Flexible cron-based job scheduling:
  - **Config Keys**: `snapshot_cron`, `healthcheck_cron`, `storage_check_cron`, `cleanup_cron`, `email_retry_cron`
  - **Cron Expression Support**: Standard 5-part cron (minute hour day month weekday)
  - **Examples**: `0 8,13,23 * * *` for 08:00, 13:00, 23:00 daily
  - **Fallback Intervals**: Cron takes precedence; interval used as fallback
  - **Dynamic Updates**: Changes applied on next config reload without restart
  
- **Persistent Job Storage** - SQLAlchemyJobStore for cross-container job visibility:
  - **Database Storage**: Jobs stored in `apscheduler_jobs` table
  - **Web App Access**: Web UI can read jobs from scheduler container
  - **Migration**: `20260309_add_apscheduler_jobs_table`

- **Settings UI - Job Scheduling Section** - New configuration panel:
  - **Cron Inputs**: Text fields for all job cron expressions with examples
  - **Interval Fallbacks**: Number inputs for interval-based scheduling
  - **Visual Design**: Indigo-themed section with helpful tooltips
  - **Quick Link**: Direct link to Job Dashboard
  - **Navigation**: "⏰ Job Management" added to sidebar

- **UI/UX Redesign** - Modernized admin interfaces with consistent design language:
  - **Audit Logs Page** (`/audit-logs`):
    - **5 Stats Cards**: Total Logs, Today's Logs, Unique Users, Top Action, with color-coded icons
    - **Advanced Filtering**: Search by user/target, action type dropdown, date range picker
    - **Quick Date Buttons**: Today, Yesterday, Last 7 Days
    - **Active Filter Tags**: Visual indicators with remove buttons
    - **Modern Table Design**: Alternating rows, hover effects, avatar initials
    - **Action Badges**: Color-coded with icons (create, update, delete, login, logout)
    - **Enhanced Pagination**: Icon-based navigation (First, Prev, Next, Last)
    - **JSON Syntax Highlighting**: In detail modal with copy button
    - **Export to CSV**: Download filtered or all logs
  
  - **WhatsApp Whitelist Page** (`/admin/whitelist`):
    - **5 Stats Cards**: Total, Active, Inactive, Admins, Users
    - **Modern Table**: Avatar initials, role badges with icons, toggle switches for status
    - **Modal-based Forms**: Add/Edit entries in styled modals with backdrop blur
    - **Improved UX**: Real-time search, toast notifications, reset filters
  
  - **Email Logs Page** (`/email-logs`):
    - **5 Stats Cards**: Total Emails, Success, Failed, Success Rate, Today
    - **Export Functionality**: Export Filtered or Export All to CSV
    - **Advanced Filtering**: Camera search, status filter, reason filter, date range
    - **Visual Badges**: Status badges with icons, reason badges with emoji
    - **Error Details Modal**: Click to view full error messages

### Fixed
- **Scheduler Database Job Store Conflict** - Fixed `ConflictingIdError` on restart:
  - Error: `duplicate key value violates unique constraint "apscheduler_jobs_pkey"`
  - Cause: SQLAlchemyJobStore persists jobs to database but `add_job()` lacked `replace_existing=True`
  - Fix: Added `replace_existing=True` to all 8 `scheduler.add_job()` calls in `start_scheduler()`
  - Scheduler now handles restarts gracefully with persistent job storage

- **Migration Table Exists Error** - Fixed `apscheduler_jobs` table already exists:
  - Error: Migration failed when table created by SQLAlchemyJobStore already exists
  - Fix: Added `table_exists()` and `index_exists()` checks in migration
  - Migration: `20260309_add_apscheduler_jobs_table.py` now idempotent

- **Snapshot Gallery 500 Errors** - Fixed UUID string conversion bug:
  - `Snapshot.id` is UUID (string) but was being converted to `int` causing errors
  - Fixed in `app/routes/snap_gallery.py` and `app/utils/snapshot_service.py`
  - Capture snapshot and delete snapshot now work correctly

- **Toast Notification Integration** - Replaced old notification system:
  - **Snapshot Gallery**: Uses `Toast.success()` and `Toast.error()` from `/static/js/toast.js`
  - Fixed JavaScript scope issues - functions now global for HTML onclick handlers

### Changed
- **Audit Logs Pagination** - Fixed pagination click handlers:
  - Moved `fetchLogs` to global scope for HTML onclick access
  - Pagination now fully functional

- **Toast Notification System** - Real-time user feedback with WebSocket:
  - **4 Notification Types**: Success (✅), Error (❌), Warning (⚠️), Info (ℹ️)
  - **6 Positions**: top-right, top-left, top-center, bottom-right, bottom-left, bottom-center
  - **Action Buttons**: Interactive notifications with clickable actions
  - **WebSocket Integration**: Real-time delivery without page refresh
  - **Dark Mode Support**: Seamless theme switching
  - **Progress Bar**: Visual countdown for auto-dismiss
  - **Persistent Notifications**: Stay until user dismisses
  - **Pre-built System Notifications**:
    - Camera online/offline alerts
    - Snapshot saved notifications
    - Storage warning/critical alerts
  - **Demo Page**: `/demo/toast/` for testing all features
  - **Integrated Pages**:
    - ✅ Snapshot Gallery - Capture/delete notifications
    - ✅ Camera Management - CRUD operation notifications
    - ✅ Settings/Config - Save confirmation notifications
  - **Service Layer**: `NotificationService` for easy backend integration
  - **Client-Side API**: JavaScript `Toast.*` methods for frontend usage

- **Logs Viewer Redesign** - Modern, feature-rich log viewing interface:
  - **Smart Log Parsing** - Parses log format `[timestamp] [LEVEL] [logger] [pid=X] message`
  - **Real-time Filtering**:
    - Search within log messages (debounced, 300ms)
    - Filter by log level (ERROR, WARNING, INFO, DEBUG)
    - Filter by logger name (dropdown populated dynamically)
  - **Auto-refresh Mode** - Toggle live update every 5 seconds with visual indicator
  - **Color-coded Log Levels**:
    - 🔴 ERROR - Red badge with bold text
    - 🟡 WARNING - Yellow badge
    - 🔵 INFO - Blue badge
    - ⚪ DEBUG - Gray badge
  - **Log Statistics Panel**:
    - Total line count
    - Entry count per log level
    - File size and last modified info
  - **Improved Navigation**:
    - Sidebar with log file list (main, snapshot, healthcheck, scheduler, management)
    - Quick scroll to Top/Bottom buttons
    - Auto-scroll when near bottom
  - **File Operations**:
    - Download log files
    - Clear filters button
    - Active filter indicator

- **Recipients UI Improvements** - More compact layout and confirmation modal:
  - **Compact Design**: Reduced padding, smaller fonts, tighter spacing throughout
  - **Confirmation Modal**: Tailwind-styled modal when sending test email
    - Shows selected group and location
    - Cancel / Confirm options
    - Prevents accidental sends
  - **Better Visual Hierarchy**: Clearer section separation with consistent styling

- **WhatsApp Gateway Integration** - GoWA (Aldinokemal) support for notifications and bot:
  - **Configuration** (`/config`):
    - Enable/disable WA notifications
    - GoWA base URL (e.g., `http://localhost:3000`)
    - **API Key (Optional)**: Only if GoWA runs with `-e AUTH_TOKEN=xxx`
    - Default receiver phone number(s), comma separated
    - Test connection button
    - Built-in setup guide
  - **Scheduled Reports**:
    - Daily camera status report (08:00 AM) - cameras without snapshots
    - Storage alerts every 2 hours when critical
  - **WhatsApp Bot** (`/webhook/gowa`):
    - Commands: `help`, `status`, `cameras`, `health`, `snapshot`, `report`
    - Real-time system status via chat
    - Camera health check via WA
    - Daily report summary
  - **Services** (`app/utils/wa_gateway.py`):
    - `WAGatewayService` - Send text, image, document
    - `WABotHandler` - Command parsing and response
    - Phone number formatting helper

### Fixed
- **Email Sent Counter Bug** - Fixed email statistics counting failed emails as sent:
  - Updated `_total_count()` and `_daily_counts()` helpers to support `success_only` filter
  - Updated `_total_count()` calls for email to use `success_only=True`
  - Updated `_daily_counts()` calls for email chart to use `success_only=True`
  - Updated `get_top_cameras_by_email()` to filter `success=True`
  - Email stats now only count successfully sent emails

- **Logging Configuration Bug** - Fixed missing logger registrations causing logs to not be written to files:
  - Registered missing loggers in `LOGGING_CONFIG`:
    - `storage_monitor` - Storage monitoring logs
    - `storage` - Storage stats API logs  
    - `websocket` - WebSocket connection logs
    - `auth` - Authentication middleware logs
    - `ping` - Ping utility logs
    - `app.insights` - Insights/reporting logs
  - Fixed import pattern in `storage_monitor.py` - removed duplicate imports inside functions
  - Fixed logger references in `stats.py` - using module-level loggers
  - All loggers now correctly write to `main.log` with proper formatting

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
