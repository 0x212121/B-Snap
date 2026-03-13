# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Fixed

### Recipient pagination issue

## [1.13.0] - 2026-03-12

### Added

#### Job Management & Scheduling
- **Job Management Dashboard** (`/admin/jobs`) for monitoring and controlling scheduled background jobs.
- **Job execution tracking** using `JobExecutionLog` with execution history, duration, and status logging.
- **Cron-based scheduling system** supporting dynamic cron updates.
- **Persistent job storage** using `SQLAlchemyJobStore` (`apscheduler_jobs` table).
- **Settings UI section for Job Scheduling** with cron configuration and interval fallbacks.

#### Reporting & SLA Monitoring
- **Executive PDF Report Generator** (`POST /health/history/report`)
  - Professional health reports with executive summary
  - PDF, Excel, and CSV export formats
  - Charts and device uptime summaries
- **SLA & Compliance Dashboard** (`/health/sla-report`)
  - SLA compliance monitoring (default 99.5%)
  - MTTR and MTBF calculations
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

#### Orphaned Files Management
- **Orphaned Files Management UI** (`/orphaned-files`) for managing snapshot files without database records.
- **Folder Scanner** to detect orphaned snapshot files on disk.
- **OrphanedFile database table** to track orphaned files with metadata (path, size, camera_id, status).
- **Dual detection methods**
  - Database-triggered orphan marking when cameras are deleted.
  - Scheduled disk scanner job comparing filesystem vs database records.
- **Management UI Features**
  - Storage summary showing total files and disk usage
  - File list with status filters (pending, reviewed, deleted)
  - Manual scan trigger
  - Dry run preview before deletion
  - Bulk cleanup with confirmation
  - Force cleanup option
  - File review flag for audit trail
- **Automatic cleanup** of empty snapshot folders after deletion.
- **API Endpoints** `/api/orphaned-files/*` for scanning, cleanup, summary, and review.
- **Navigation**: Added **"🧹 Orphaned Files"** to Administration menu.
- **Database migrations**
  - `20260310_orphaned_snapshots`
  - `20260311_add_orphaned_files`
  - `20260311_fix_snapshot_fk_cascade`

#### UI / UX Improvements
- **Audit Logs page redesign** (`/audit-logs`)
  - Stats cards, advanced filtering, and CSV export
  - Improved pagination and JSON detail viewer
- **WhatsApp Whitelist page improvements** (`/admin/whitelist`)
  - Stats overview, role badges, toggle switches, modal forms
- **Email Logs dashboard improvements** (`/email-logs`)
  - Status metrics, filtering, CSV export, and error details modal
- **Logs Viewer redesign**
  - Smart log parsing
  - Real-time filtering
  - Log statistics panel
  - Auto-refresh capability
- **Recipients UI improvements**
  - Compact layout
  - Confirmation modal for test email sending

---

### Fixed

- Fixed **scheduler restart conflict (`ConflictingIdError`)** when jobs already exist in database.
- Fixed **migration error** when `apscheduler_jobs` table already exists.
- Fixed **Snapshot Gallery 500 errors** caused by incorrect UUID conversion.
- Fixed **toast notification JavaScript scope issues**.
- Fixed **audit log pagination handlers**.
- Fixed **email statistics counting failed emails as sent**.
- Fixed **missing logger registrations** preventing logs from being written properly.

Additional fixes:
- Fixed **Job Management API** missing `HTTPException` import in `app/routes/jobs.py`.
- Fixed **Job Stats JSON serialization error** caused by SQLAlchemy `Decimal` values.
- Updated **SQLAlchemy `case()` syntax** to match SQLAlchemy 2.0 API.
- Fixed **CSV camera upload bug** where cameras were counted but not actually created.
- Fixed **Camera Locations API (`/camera-locations`)** 500 error caused by nullable fields.

---

### Changed

- Replaced legacy notification system with **real-time toast notifications**.
- **Logs viewer interface redesigned** with improved filtering and navigation.
- **Recipients UI redesigned** with compact layout and confirmation modal.

Additional changes:
- **Job Schedule Modal** replaced browser `prompt()` with modern Tailwind modal:
  - Backdrop blur and rounded design
  - Styled cron input field
  - Cron examples panel
  - Keyboard shortcuts (Enter to save, Escape to close)
  - Consistent UI with other application modals.

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
