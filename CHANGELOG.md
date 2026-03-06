# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
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
