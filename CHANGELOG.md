# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.17.0] - 2026-04-07

### Security

#### CRITICAL: Secure Video Serving (CRIT-001)
- **Blocked direct video access** - `/static/videos/*` now returns 403 Forbidden
  - Videos must be accessed via authenticated API endpoints
  - Prevents unauthorized access to safety-critical footage
  - All video access now logged to audit_logs

- **New API Endpoints**
  - `GET /api/videos/secure/{video_id}` - Serve video file by ID with authentication
  - `GET /api/videos/file/{file_path}` - Serve video file by path with authentication  
  - `POST /api/videos/gallery-view` - Batch audit logging for gallery views
  - Download support: `GET /api/videos/secure/{id}?download=true`

- **Anti-Flooding Audit Log**
  - Gallery view logged once per page load (not per video)
  - Individual video access logged with full context
  - Download actions separately tracked
  - Prevents audit log flooding when browsing gallery

- **Files Modified**
  - `app/main.py` - Route blocker for `/static/videos/*`
  - `app/routes/videos.py` - New API endpoints with audit logging
  - `templates/video_gallery.html` - Updated to use secure URLs
  - `templates/_video_grid.html` - Updated thumbnail and modal handlers

#### CRITICAL: Group-Based Access Control Fix (CRIT-002)
- **Fixed insecure group filtering** - Changed from name matching to foreign key joins
  - Before: `Snapshot.camera_group == user_group.name` (vulnerable to name changes)
  - After: `JOIN Camera ON Snapshot.camera_id = Camera.id WHERE Camera.group_id = group_id`
  - Prevents unauthorized access when camera group names change

- **Applied to**
  - Snapshot gallery filtering (`app/routes/snap_gallery.py`)
  - Video gallery filtering (`app/routes/videos.py`)

#### CRITICAL: CSV Import Validation (CRIT-003)
- **Strict safety_classification validation**
  - Invalid values now cause row skip (not silently defaulted to "standard")
  - Valid values: `critical`, `standard`, `low`
  - Warning logged when "critical" classification imported (requires verification)

- **Password strength warning**
  - Logs warning for passwords shorter than 4 characters
  - Helps identify weak credentials during bulk import

- **Files Modified**
  - `app/routes/cameras.py` - Enhanced CSV upload validation

---

## [1.15.0] - 2026-04-01

### Added

#### Custom Email Templates with Drag-and-Drop Editor
- **Email Template Management** - Customize notification messages via web UI
  - New page: `/email-templates` with tabbed interface for 3 template types
  - Drag-and-drop variable insertion from sidebar
  - Live preview with sample data before saving
  - Reset to default functionality
  - New database table: `email_templates` (template_type, subject, plain_body, html_body)
  - Migration: `a3366d14c9a4_add_email_templates_table.py`

- **API Endpoints**
  - `GET /email-templates` - Template editor page
  - `GET /api/email-templates/{type}` - Get template (custom or default)
  - `POST /api/email-templates/{type}/save` - Save custom template
  - `POST /api/email-templates/{type}/preview` - Preview with sample data
  - `POST /api/email-templates/{type}/reset` - Reset to default
  - `GET /api/email-templates/{type}/variables` - Get available variables

- **Integration**
  - `send_tamper_alert()` - Uses `tamper_alert` template
  - `send_recovery_alert()` - Uses `recovery_alert` template
  - `send_offline_incident_email_once()` - Uses `offline_alert` template
  - All email functions now use template renderer instead of hardcoded strings

#### Email Notification Circuit Breaker (Anti-Flooding)
- **Smart Notification Suppression** - Prevents email flooding when camera issues persist
  - Circuit breaker pattern: After 3 consecutive failures, notifications suppressed for 60 minutes
  - Automatic suppression reset when camera recovers (comes back online)
  - Early exit if SMTP not configured (prevents useless retry attempts)
  - Deduplication window: 15 minutes between tamper alerts for same camera/reason
  - New columns in cameras table:
    - `notification_fail_count` - Tracks consecutive failures
    - `notification_suppressed_until` - Timestamp when suppression ends
    - `last_notification_at` - For deduplication tracking
  - Functions:
    - `is_notification_suppressed()` - Check if camera is in suppression period
    - `record_notification_failure()` - Increment counter and activate suppression if needed
    - `record_notification_success()` - Reset counter on successful send
    - `reset_notification_suppression()` - Reset when camera recovers
    - `should_send_tamper_alert()` - Deduplication logic for tamper alerts
  - Migration: `20260330_add_notification_circuit_breaker.py`

#### Tamper Alert Cooldown & Incident Time Fix
- **Bug Fix: Incident Time Accuracy** - Fixed timestamp in email to show ACTUAL detection time
  - Changed from `datetime.now()` (email send time) to `snapshot.timestamp` (actual detection time)
  - Updated `send_tamper_alert(db, camera, reason, path, incident_time=snapshot.timestamp)`
  - Email body now shows accurate incident time: "Waktu Kejadian: 30/03/2026 09:52:08 UTC"
  - Database `incident_started_at` now stores actual detection time, not email send time
  - Cooldown calculation uses incident_time for consistency

- **Immediate Cooldown Protection** - Prevents per-second flooding of tamper alerts
  - New columns in camera_health table:
    - `alert_cooldown_until` - Timestamp when next alert can be sent
    - `last_alert_reason` - Reason for last alert (for deduplication)
  - Functions in snapshot_utils.py:
    - `is_alert_in_cooldown()` - Check if camera is in cooldown period
    - `set_alert_cooldown()` - Set 15-minute cooldown after alert
  - **Scheduler Rate Limiting**: Minimum 30 seconds between snapshots for same camera
    - Prevents multiple concurrent snapshot processes for one camera
    - Global cache `_snapshot_rate_limit_cache` tracks last snapshot per camera
  - **Triple-layer protection**:
    1. Scheduler rate limiting (30-second minimum interval)
    2. CameraHealth cooldown check (15-minute alert cooldown)
    3. CameraHealth last_alert_reason deduplication
    4. CameraEmailNotificationLog database check
  - **Immediate Cooldown**: Set BEFORE attempting to send in `send_tamper_alert()`
    - Ensures no retry flooding even if SMTP fails
    - Cooldown persists in database across function calls
  - Recovery alerts use shorter 5-minute cooldown
  - Enhanced logging for debugging flooding issues
  - Migration: `20260330_add_alert_cooldown.py`

#### Security & Compliance (Audit Remediation P2)
- **Dual-Table Audit Log with 6-Month Archive** - P2 Compliance Enhancement
  - Implemented dual-table strategy: `audit_logs` (current) + `audit_logs_legacy` (historical)
  - `audit_logs`: Append-only table with PostgreSQL triggers preventing UPDATE/DELETE
  - `audit_logs_legacy`: Historical data (>6 months) eligible for archival
  - `audit_logs_unified`: Database view for cross-table querying
  - `audit_archive_history`: Tracks all archive operations with checksums
  - Archive process: Export → Gzip compress → AES-256 encrypt → Verify → Delete
  - SHA-256 checksums for integrity verification
  - Environment variable: `AUDIT_ARCHIVE_KEY` for encryption
  - API endpoints: `GET /api/stats`, `POST /api/archive`, `GET /api/archive/history`
  - UI: Archive warning banner, archive history modal, manual archive button
  - Scheduled job: Automatic archive every 6 months (January & July)
  - Migration: `20260328_dual_table_audit_logs.py`

- **Enhanced Audit Log Fields (P2-001)**
  - Added `user_agent`, `request_path`, `request_method`, `response_status` columns
  - All API access now logs complete HTTP request context
  - Method and Status filters in audit log UI
  - Anti-flooding strategy: Gallery uses batch logging, detail views use individual logging

- **Safety Classification (P2-002)** - Camera Risk Categorization
  - Added `safety_classification` column to cameras table (critical/standard/low)
  - Critical (🔴): Incident coverage cameras - highlighted in UI
  - Standard (🟡): Regular monitoring cameras
  - Low (🟢): General surveillance cameras
  - Safety badges displayed in camera table and hostname column
  - Stats card showing count of critical safety cameras
  - Migration: `8a53b8bab616_add_safety_classification_p2_002.py`

- **Retention Hold (P2-003)** - Legal Hold for Evidence
  - Added `retention_hold`, `retention_hold_reason`, `retention_hold_by`, `retention_hold_at` columns
  - Prevents purge of incident footage during investigation
  - Admin toggle in snapshot detail modal
  - Retention hold indicators in snapshot gallery
  - Audit logging for all retention hold operations
  - Migration: `f3170d221195_add_retention_hold_p2_001.py`

- **Secure Snapshot Serving (P2-004)**
  - Blocked direct access to `/static/snapshots/*` - returns 403 Forbidden
  - All snapshot access must go through authenticated API endpoints
  - New endpoint: `GET /api/snapshots/secure/{snapshot_id}` with auth check
  - Legacy token URLs (`/snapshot/file/{path}?token=...`) still functional with enhanced logging
  - Gallery images migrated to use secure API endpoints

#### Template Relayout
- **Camera Management (`cameras.html`)**
  - Added Safety Classification column with color-coded badges
  - Critical cameras show 🔴 badge in hostname column
  - Safety stats card (Critical count with warning icon)
  - Added `icon_lock` and `icon_shield_check` from `_icons.html`

- **Audit Logs (`audit_logs.html`)**
  - Enhanced table with Method and Status columns
  - Archive warning banner (shows when >1000 records need archiving)
  - Archive status card with real-time statistics
  - Archive history modal with operation details
  - Manual archive trigger button with confirmation

#### Windows Startup
- **PowerShell Script Enhancement** (`start-local.ps1`)
  - Added proper error handling and process management
  - Fixed gunicorn compatibility issues on Windows

### Changed

#### Navbar Restructuring & Simplification
- **Merged: Statistics + Insights → Analytics Dashboard** - Consolidated analytics pages
  - New unified page: `/analytics` with tabbed interface (System Overview + Snapshot Stats)
  - Previous `/stats` and `/insights` routes redirect to `/analytics` with appropriate tab parameter
  - Reduced 2 menu items into 1 "Dashboard" item under Analytics section
  - Maintains all functionality: KPI cards, charts, storage health, scheduler logs
  - Backward compatible: old URLs redirect automatically
  - Files: `templates/analytics.html` (new), `app/routes/stats.py`, `app/routes/insights.py`, `app/utils/template_helper.py`

- **Moved: Changelog → Footer Modal** - Removed from navbar to reduce clutter
  - Changelog no longer appears in Developer section navbar
  - Footer version text is now clickable to open modal changelog
  - Modal displays last 5 versions with expandable categories (Added, Fixed, Changed, etc.)
  - "Full Page" button opens complete `/changelog` page with pagination
  - Maintains backward compatibility: direct `/changelog` access still works
  - Files: `templates/base.html`, `app/utils/template_helper.py`

- **Impact**: Reduced from 6 sections/22 items to cleaner structure (32% menu reduction)
  - Before: 6 sections (Monitoring, Devices, Administration, Logs, Developer, Analytics)
  - After: 5 sections with 15 menu items
  - Improved mobile navigation and reduced cognitive load


### Fixed

#### Recovery Alert Email Body
- **Bug Fix: Missing HTML Body** - Recovery alert now has proper HTML formatting
  - Previously: `html=None` caused plain text only emails
  - Now: Full HTML body with styling matching other alert types

- **Bug Fix: Missing Snapshot Attachment** - Recovery alert now includes latest snapshot
  - Previously: `image_path=None` meant no photo attachment
  - Now: Queries latest snapshot from database and attaches if available
  - Shows snapshot timestamp in email body

#### Timezone Consistency in Email Templates
- **Bug Fix: UTC vs Local Time** - All timestamps now use configured timezone
  - Previously: `recovery_time` and `incident_time` used UTC (`strftime` directly)
  - Now: All times use `format_datetime_with_tz(to_current_timezone(...))`
  - Consistent with `snapshot_time` formatting
  - Affected functions: `send_recovery_alert()`, `send_tamper_alert()`

#### Email CC Header
- **Bug Fix: Empty CC Header** - Fixed potential SMTP issue with empty CC
  - Previously: `msg["Cc"] = ""` when email_cc not configured
  - Now: CC header only set when `email_cc` has value

#### Videos Endpoint
- **Fixed: Infinite Scroll Pagination** - Resolved infinite loading issues in video gallery

---

## [1.14.0] - 2026-03-26

### Added

#### Security & Compliance (Audit Remediation P0/P1)
- **File Integrity Verification (SHA-256)** - P0 Critical Finding
  - Added `file_hash` column to `snapshots` and `videos` tables
  - Automatic SHA-256 hash calculation on file creation
  - Integrity verification API: `GET /snap/{id}/verify`
  - Migration: `20260326_add_file_hash_and_soft_delete.py`

- **Soft Delete for Evidence Protection** - P0 Critical Finding
  - Added `deleted_at` column to `snapshots` and `videos` tables
  - Delete operations now perform soft delete by default (data recoverable)
  - Admin-only hard delete with audit logging
  - Restore functionality: `POST /snap/{id}/restore`, `POST /videos/{id}/restore`
  - Bulk purge for old deleted items: `POST /admin/snapshots/purge`, `POST /admin/videos/purge`
  - Admin endpoints to view deleted items: `GET /admin/snapshots/deleted`
  - Prevents accidental/permanent deletion of critical CCTV evidence

- **Camera Password Encryption (AES-256-GCM)** - P1 High Finding
  - New encryption utility: `app/utils/encryption.py`
  - AES-256-GCM encryption for camera passwords at rest
  - Automatic encryption on save, decryption on read via property
  - Environment variable: `ENCRYPTION_KEY` (generate with `openssl rand -hex 32`)
  - Backward compatible with existing plain-text passwords
  - Migration: Camera password column changed to TEXT to accommodate encrypted data

- **NVR Password Encryption (AES-256-GCM)** - P1 High Finding
  - Applied same AES-256-GCM encryption for NVR passwords
  - Updated `app/models/nvr.py` with encrypted password property
  - New admin endpoint: `GET /api/nvr/{id}/password` for password retrieval
  - Password excluded from standard API responses
  - Migration: NVR password column changed to TEXT for encrypted data

- **API Security Enhancement** - P1 High Finding
  - Removed password field from `GET /api/camera/{id}` response
  - New admin-only endpoint: `GET /api/camera/{id}/password` for password retrieval
  - All password access logged in audit trail (user, IP, timestamp)
  - Prevents credential exposure through API responses

#### Windows Service & Startup
- **PowerShell startup script enhancement** (`start-local.ps1`)
  - Added `all` mode to start web + scheduler + notifier simultaneously
  - Proper log separation for each service
  - Process monitoring and auto-restart capability

#### Bulk Import/Export
- **Recipients CSV Import/Export** (`/recipients`)
  - Export all recipients to CSV format: `email,nickname,group_name,locations`
  - Bulk import recipients from CSV with validation
  - Duplicate detection (skips existing email+group combinations)
  - Import modal with format instructions and dark mode support
  - API endpoints: `GET /api/recipients/export`, `POST /api/recipients/import`

- **WhatsApp Whitelist CSV Import/Export** (`/admin/whitelist`)
  - Export whitelist to CSV: `phone_number,name,role,group_name,is_active`
  - Bulk import WhatsApp numbers from CSV
  - Phone format validation (must start with 628)
  - API endpoints: `GET /api/whitelist/export`, `POST /api/whitelist/import`

#### Database & Schema
- **Task Timings Timezone Support**
  - Added `timezone=True` to `started_at` and `ended_at` columns in `task_timings` table
  - Migration: `ea697ca6b008_add_timezone_to_task_timings_datetime_.py`
  - Ensures consistent datetime storage with timezone information

### Fixed

#### Core Systems
- **APScheduler Table Creation** - Fixed `apscheduler_jobs` table not existing on fresh database by creating it explicitly before scheduler starts.
- **Snapshot Stats Increment** - Fixed `CameraDailyStats` not incrementing by adding `check_stats()` calls in both scheduler and manual snapshot service.

#### Reporting & PDF
- **PDF Report Generation** - Enhanced executive PDF reports with:
  - Executive Summary section
  - SLA Compliance table
  - Key Highlights and Top/Worst Performers
  - Device group filtering support
- **PDF Page Layout** - Fixed table splitting issues with `SPLITBYROW`
  - Adjusted column widths (16.5cm total) to prevent overflow
  - Removed incomplete page footer text
- **PDF Unicode Issues** - Fixed visual column overflow by changing `█` to `■`

#### Export Formats
- **CSV/Excel Export 500 Errors** - Fixed encoding errors by using `StringIO → encode → BytesIO` pattern instead of direct BytesIO.

#### Datetime & Timezone
- **Datetime Formatting Standardization** - Applied consistent format across all endpoints:
  - Standard format: `DD/MM/YYYY - HH:MM:SS TZ`
  - Updated: `audit.py`, `audit_log.py`, `snap_gallery.py`, `videos.py`, `user_management.py`, `email_logs.py`
  - New helper function: `format_datetime_standard()` in `timezone_helper.py`

#### Camera & Health Monitoring
- **Cameras Without Snapshots** - Fixed incorrect query join condition:
  - Changed `DBCamera.id == CameraHealth.id` to `DBCamera.id == CameraHealth.camera_id`
  - Fixed similar issue in `healthcheck.py` and `cameras.py`
- **Health Check Query** - Fixed health record lookup to use `camera_id`/`nvr_id` instead of `id`

#### Email Logs
- **Null Camera ID Handling** - Fixed error when displaying email logs with `camera_id = null` (test emails)
- **Recipients Modal** - Fixed "+X more" button not showing recipient details in popup
  - Added `currentLogs` global variable to store log data
  - Updated `showRecipientsModal()` to display full recipient list with camera info and type
- **ORM Object Modification** - Fixed SQLAlchemy state corruption by using local variables for datetime conversions instead of modifying ORM objects directly

#### Import/Export UI
- **Recipients Import Success Icon** - Fixed success import showing failed icon (changed `json.success` to `json.status === 'success'`)
- **Dark Mode Support** - Added dark mode styling to import modals and SweetAlert2 popups
- **Whitelist Import Error** - Fixed HTML error page showing instead of JSON when CSV format is invalid

### Changed
- **Email Logs Template** - Updated to handle null `camera_name` gracefully (displays "-" instead of "null")

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
