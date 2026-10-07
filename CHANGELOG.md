# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Deliver all WhatsApp API-flow responses (text, image, and video), including processing messages, to the originating chat with the command quote when enabled.

- Fix WhatsApp Bot Builder Add Command on HTTP origins without `crypto.randomUUID`, and prevent Preline from initializing custom tabs in the builder, Configuration, and Record Checks.

- Allow web startup without generated documentation, include built documentation HTML when available, resolve static mounts from the application root, and smoke-check the web application import during Docker builds.

- Include HTTPX in runtime dependencies so Docker web workers can import WhatsApp workflows; reject image builds with missing top-level application import modules.

## [2.4.0] 2026-10-07

### Added

- Persistent daily API-call totals for Analytics after log cleanup.
- Read-only SMB/NFS recording mounts with admin controls, encrypted credentials, and server allowlists on Linux.
- WhatsApp Bot Builder with custom commands/API flows, media replies, previews, testing, delivery monitoring, and draft protection; video recording via API/WhatsApp.
- JSON/ORJSON log-handler benchmarks with response-equivalence checks.

### Changed

- Independent cron schedules for all 14 jobs, reload within 60 seconds, and EN/ID confirmation for manual runs.
- Bounded email retry batches/runtime, cached SMTP settings, fewer database operations, and delivery/backlog summaries in job history.
- Standardized UI components, dialogs, feedback, and pagination; compact Record Checks tabs and immediate EN/ID date updates.
- Faster JSON responses with ORJSON across logs, analytics, cameras, and galleries.
- Streamlined Docker builds and improved service readiness, shutdown, resource limits, database pools, and log rotation.
- Hardened Compose credentials/privileges and localhost ports, optional pgAdmin, persistent audit/pgAdmin data, storage monitoring, and stable archive keys.
- Run migrations/configuration initialization before services; support empty PostgreSQL databases and stop startup on migration failure.

### Fixed

- Corrected email retry migrations, queue fields/IDs, tamper/recovery parameters, and attempt limits; preserved incident times, distinguished SMTP failures from deferrals, reused logs, and cleaned up terminal tasks.
- Removed duplicate Devices Status refreshes while retaining refresh after active checks.
- Corrected heatmap, scheduler, and WhatsApp report timezones, including WIB/WITA/WIT labels.
- Improved WhatsApp capture/delivery retries and deduplication; enforced whitelist, role, and camera-group access.
- Improved API errors and moved camera-IP lookup to `/api/cctv/resolve-ip`.

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
