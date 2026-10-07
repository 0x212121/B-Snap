# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Reproducible JSONResponse/ORJSONResponse benchmark for log-content serialization and complete handler execution, with response-equivalence checks.

- Native read-only SMB/NFS recording mounts with admin controls, encrypted SMB passwords, deployment IP allowlists, and restricted Linux deployment support.
- WhatsApp Bot Builder with custom commands, API flows, and configurable text, image, and video replies.
- Live conversation previews, action testing, template variables, and message delivery monitoring.
- Video recording through APIs and WhatsApp commands.

### Changed

- Persist shared audit archives and pgAdmin state across container recreation; use application-root storage paths, monitor the footage filesystem, exclude runtime archives from image builds, and require a stable audit archive key without logging key material.
- Remove privileged mode and embedded database/admin passwords from Compose deployments; require deployment credentials, restrict database/admin ports to localhost, and make pgAdmin opt-in through the admin profile.
- Run Docker database migrations and default configuration initialization in a one-shot service before web, scheduler, and notifier; support empty PostgreSQL databases through a frozen baseline and stop startup on migration failure.
- Standardized remaining menu pages and AJAX rows with shared KPI cards, semantic badges, action icons, loading skeletons, modal controls, and accessible numbered pagination; use shared NVR row templates and clipboard feedback.
- Aligned Camera Management and Devices Status KPI cards, filters, table actions, status badges, loading states, and icons with the shared UI standard; preserve Ping All icons after completion and escape device data in rendered rows.
- Audit-log, camera analytics, camera-list, and snapshot/video gallery data responses now use ORJSON for faster JSON serialization.

- Log-content responses now use ORJSON for faster serialization while preserving payloads, status codes, and headers.

- Health, galleries, audit/email logs, and other frontend date/time displays now follow the selected English/Indonesian language instead of a fixed locale or the browser language.

- Job Management now shows both date and time for Next Run and formats timestamps using the selected English/Indonesian language, updating immediately when the language changes.

- All 14 jobs in Job Management now support independent cron schedules, full cron validation, and automatic schedule reload within 60 seconds while preserving legacy defaults.

- Unified page styling, simpler navigation, and clearer destructive actions.
- Improved Bot Builder editing, search, draft protection, and English/Indonesian support.
- Simplified release notes across all versions to highlight important changes.

### Fixed

- Prevent a second Devices Status table refresh on page load when no health check is running; retain the refresh after an active check or Ping All completes.

- Snapshot heatmap now uses the configured timezone and correctly handles naive UTC timestamps without raising a timezone TypeError.

- Scheduler triggers and WhatsApp camera report dates now follow the configured timezone, including timezone changes on configuration reload. WhatsApp camera and NVR report date headers display the timezone abbreviation (WIB/WITA/WIT).

- Improved WhatsApp capture, media delivery, retries, and duplicate-message handling.
- Enforced sender whitelist, role, and camera-group access across commands and tests.
- Improved API error reporting and moved camera-IP lookup to `/api/cctv/resolve-ip`.

## [2.3.0] - 2026-09-29

### Added

- English/Indonesian interface switching.
- Camera availability, downtime, uptime, and group-filtered analytics with PDF reports.
- Archive History browsing and SMTP connection testing.

### Changed

- Improved configuration pages and analytics performance.
- Preserved archived audit logs and removed storage alert notifications.

### Fixed

- Corrected timezone handling, snapshot periods, and API error feedback.

## [2.2.0] - 2026-09-24

### Added

- Multiple camera groups per camera, including editing and CSV import/export.
- Group-aware access and notifications across cameras, snapshots, videos, and reports.

## [2.1.1] - 2026-09-17

### Fixed

- Prevented stalled camera captures from blocking scheduled snapshots.
- Improved capture timeouts, cleanup, and partial-job reporting.

## [2.1.0] - 2026-07-16

### Added

- Recording-folder monitoring with camera mapping and WhatsApp alerts/recovery notifications.
- Missing email-recipient coverage reports with Excel export.
- WhatsApp group receiver discovery and stronger record-source deletion confirmation.

### Fixed

- Improved GoWA compatibility, configuration saving, and daily reports.
- Corrected recording-folder status tracking and preserved manual camera mappings.
- Improved secure video playback, thumbnail generation, and job/API metrics.

## [2.0.2] - 2026-06-09

### Fixed

- Improved direct snapshot URL capture and camera authentication support.
- Fixed Remember Me sessions expiring on long-running monitoring pages.

## [2.0.1] - 2026-04-10

### Fixed

- Restored All Groups assignment for WhatsApp whitelist entries.
- Improved video thumbnails, playback, and file-size display.
- Improved map refresh performance and timezone-aware analytics.
- Reduced blank snapshots and capture failures on unstable cameras.

## [2.0.0] - 2026-04-07

### Added

- Automated snapshot/video retention with retention-hold protection.
- Environment and security documentation.

### Security

- Required authenticated video access with audit logging.
- Fixed camera-group access checks and enforced API token expiration.
- Strengthened CSV/GPS validation and aligned session expiration.

## [1.15.0] - 2026-04-01

### Added

- Custom email templates with variable insertion and live previews.
- Notification cooldowns and failure suppression to prevent email flooding.
- Camera safety classification and retention holds for incident evidence.

### Changed

- Unified Statistics and Insights into the Analytics dashboard.

### Fixed

- Corrected email incident times, timezone formatting, and recovery attachments.
- Fixed video gallery pagination and improved Windows startup.

### Security

- Authenticated snapshot access and append-only audit logs with encrypted archives.

## [1.14.0] - 2026-03-26

### Added

- Recoverable snapshot/video deletion and file integrity verification.
- CSV import/export for email recipients and WhatsApp whitelist entries.
- Improved Windows service startup and executive PDF reports.

### Fixed

- Corrected scheduler initialization, snapshot statistics, health checks, and exports.
- Improved timezone consistency and email log handling.

### Security

- Encrypted camera/NVR passwords and restricted credential retrieval to admins.

## [1.13.0] - 2026-03-12

### Added

- Job Management with execution history and configurable schedules.
- SLA monitoring, executive reports, and scheduled report delivery.
- GoWA WhatsApp integration and real-time toast notifications.
- Orphaned snapshot file detection and cleanup.

### Changed

- Improved audit, email, whitelist, and log management interfaces.

### Fixed

- Corrected scheduler restarts, snapshot gallery errors, camera imports, and reporting metrics.

## [1.12.0] - 2026-03-06

### Added

- Storage monitoring with usage trends, alerts, and capacity forecasts.
- Configurable log retention and manual cleanup.

### Fixed

- Improved startup, configuration validation, and migration reliability.

### Security

- Required password verification and audit logging for manual cleanup.

## [1.11.0] - 2025-10-24

### Added

- Video watermarks and richer email alert details.
- Executive reports from the Insights dashboard.

## [1.10.2] - 2025-10-16

### Added

- Standalone camera support on maps.

### Fixed

- Corrected email recipient pagination.

## [1.10.1] - 2025-10-10

### Changed

- Updated documentation and resolved minor bugs.

## [1.10.0] - 2025-10-09

### Added

- Usage Insights dashboard with API activity and chatbot command analytics.

### Changed

- Improved interface consistency.

## [1.9.1] - 2025-10-08

### Fixed

- Corrected email notifications sent to the wrong group.

## [1.9.0] - 2025-10-08

### Added

- Email alerts, delivery logs, and recipient management APIs.
- Custom error pages.

### Changed

- Refreshed icons and page layouts.
