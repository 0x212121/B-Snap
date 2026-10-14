# Changelog

All notable changes to this project will be documented in this file.  
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [Unreleased]

### Added

- Maps historical Offline heatmap with 24-hour/7-day/30-day periods, group-restricted history, clipped ongoing incidents, incomplete-history feedback, localized timezone headers, and per-camera downtime details.
- Maps Focus issues and All cameras buttons with automatic camera bounds, Offline/High Latency filtering, synchronized status checkboxes, live EN/ID labels, and feedback when no cameras can be located.
- Maps base-layer selector for OpenStreetMap streets, Esri satellite imagery, and dark-styled OpenStreetMap tiles, with saved browser preference, live EN/ID labels, and dark-mode controls.

### Removed

- Removed the Maps zone/site dropdown and client-side camera-group filtering.

## [2.5.0] - 2026-10-14

### Added

- Record HTTP(S) HLS `.m3u8` streams from the camera's Snapshot URL, including authentication and query strings, without requiring camera ping/ONVIF; preserve RTSP recording and watermarking.

### Changed

- Added a per-command HD image-quality toggle for Capture New Snapshot and Send Latest Snapshot, including fallback images and scheduled delivery through GoWA.
- Added EN/ID examples of conditional `if`/`else` response formatting to the WhatsApp Bot Builder frontend.
- Added `{{#if variable}}...{{else}}...{{/if}}` support to WhatsApp command response templates, including conditions inside `each` loops.
- Added optional `include_without_ip=true` to `/api/cctv/resolve-ip` to include matching cameras with null or empty IP addresses while preserving access restrictions.
- Sorted WhatsApp Show help command lists alphabetically by trigger, including custom response templates.

### Fixed

- Dynamically display snapshot/video sizes and the Trash Management total in KB below 1024 KB and MB from 1024 KB onwards; convert raw byte values correctly and preserve numeric size sorting.
- Preserve authentication cookies and session data on temporary database/endpoint failures; return 503 without replaying requests, and rotate Remember Me tokens atomically.
- Cleaned up Configuration route formatting, annotations, and unused imports while preserving admin authorization, validation, uploads, SMTP tests, and manual cleanup behavior.
- Deliver recording completion popups once through the database notification queue, only to the user who started recording from the browser; suppress web popups for bot/API recordings and validate recipient sessions before delivery.
- Stack toast notifications using their actual heights and consistent spacing; preserve center alignment, recalculate after resizing, and evict the oldest active toast correctly during bursts.

### Removed

- Removed Snapshot Interval from Configuration; snapshot scheduling is managed through Job Management, and saving configuration preserves the legacy interval fallback.

## [2.4.1] - 2026-10-13

### Changed

- Removed unused environment variables and unimplemented placeholders from `.env.example`; corrected SMTP fallback names to `SMTP_PASS` and `SMTP_SECURITY`, and clarified process versus application timezone.

### Fixed

- Preserved scheduled job IDs and history when triggers change; corrected slash-trigger history URLs and command execution.
- Completed EN/ID gallery feedback and shared dialogs, including Bot Builder details and live language updates.
- Prevented Audit Logs advanced filters from squeezing or shifting the main controls when expanded, with responsive wrapping.
- Fixed Audit Logs CSV export returning 404 by generating the download URL from the registered backend route.

- Added the CIFS helper's required `DAC_READ_SEARCH` capability to the native recording mount overlay and AppArmor profile, preventing capability setup failures before SMB mounts.

## [2.4.0] - 2026-10-12

### Added

- WhatsApp Bot Builder with custom commands/API flows, cron or daily scheduled delivery, media replies, previews, testing, delivery monitoring, and draft protection; video recording via API/WhatsApp.
- Live EN/ID cron descriptions, scheduler-compatible validation, and timezone labels across Configuration, Job Management, and Bot Builder.
- Persistent daily API-call totals for Analytics after log cleanup.
- Read-only SMB/NFS recording mounts with admin controls, encrypted credentials, and server allowlists on Linux.
- JSON/ORJSON log-handler benchmarks with response-equivalence checks.

### Changed

- Independent cron schedules with reload within 60 seconds, per-command WhatsApp job history, and EN/ID confirmation for manual runs.
- Bounded email retry batches/runtime, cached SMTP settings, and delivery/backlog summaries; pending notifications expire after a configurable 24 hours with recorded cancellation reasons and seven-day terminal cleanup.
- Consolidated daily NVR record-check reports into one previous-day summary and PDF per WhatsApp recipient, with historical status counts, isolated source failures, and upload retries.
- Standardized UI components, page titles, dialogs, feedback, and pagination; compact Record Checks tabs and immediate EN/ID date and schedule updates.
- Faster JSON responses with ORJSON across logs, analytics, cameras, and galleries.
- Improved Docker builds, readiness, shutdown, resource limits, database pools, and log rotation; hardened Compose credentials/privileges and localhost ports, optional pgAdmin, persistent audit/pgAdmin data, and storage monitoring.
- Run migrations/configuration initialization before services, support empty PostgreSQL databases, and stop startup on migration failure.

### Removed

- WhatsApp daily camera-status report job and schedule controls; retained the NVR record-check daily report.

### Fixed

- Corrected email retry migrations, queue fields/IDs, tamper/recovery parameters, and attempt limits; preserved incident times, distinguished SMTP failures from deferrals, reused logs, and cleaned up terminal tasks.
- Corrected heatmap, scheduler, job-history, and WhatsApp report timezone handling, including WIB/WITA/WIT labels and explicit UTC offsets independent of browser timezone or language.
- Improved WhatsApp capture/delivery retries and deduplication; enforced whitelist, role, and camera-group access; sent API-flow text/media/processing replies to the originating chat with configured quotes.
- Fixed Bot Builder command creation on HTTP origins without `crypto.randomUUID` and custom tab initialization; removed duplicate Devices Status refreshes while retaining refresh after active checks.
- Improved API errors and moved camera-IP lookup to `/api/cctv/resolve-ip`.
- Fixed web startup without generated documentation, bundled built documentation and runtime HTTPX, resolved static mounts from the application root, and added Docker application-import checks.

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
