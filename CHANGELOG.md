# Changelog

All notable changes to B-Snap will be documented in this file.

## [1.7.0] - 2025-08-13

### Added
- GPS Map Display: Added the ability to display CCTV locations on a marker popup
- Notes Field: Added a 'Notes' field to the Camera and NVR management forms to store additional information.
- Token Security Log: The client IP address is now recorded in the audit log when an admin revokes a user's API token to improve security.

### Changed
- Password Visibility Icon: Updated the password field's visibility toggle icon for better usability and intuition.
- Audit Log Format: The audit log for data updates (User, NVR, Camera) now displays a concise summary of changes instead of a full JSON dump, making it more compact and readable.
- Snapshot Database Relation: Modernized the database structure by changing the snapshot log table's relationship from hostname to camera_id, improving data integrity and overall system stability.

### Fixed
- Automatic Scheduler: Fixed the scheduled task responsible for automatically deleting old statistics.
- Snapshot Feature: Fixed the 'View Snapshot' and 'Realtime Snapshot' features, which were broken after the database structure update.
- Camera Edit Bugs: Resolved several bugs in the camera edit form, including an issue with saving the port and a case-sensitive validation error for unique hostnames.
- API Token Sorting: Corrected the API token sorting logic on the user management page to reliably display the most recent tokens first.
- Frontend Error Handling: Improved the apiPost function in JavaScript to display more informative error messages from the backend, simplifying future debugging processes.

## [1.6.3] - 2025-08-07

### Added
- Search bar in maps view.

### Changed
- Snapshot error responses now include the camera IP address.
- Removed "items per page" option from the config menu.

### Fixed
- Corrected HTML layout issues.


## [1.6.2] - 2025-08-04

### Fixed
- Fixed memory leak issue during snapshot process.

## [1.6.1] - 2025-08-01

### Added
- Snapshot batch size and batch delay in config.
- Edit whitelist user

### Changed
- Snapshot scheduler with interval 5s between batch.

### Fixed
- User name now correctly restored from session token after app restart
- Session automatically rehydrates missing `user_name` from `user_id` if session is partially corrupted
- Fixed redirect from gallery to maps caused by missing `user_groupid` in restored session


## [1.6.0] - 2025-07-31

### Added
- New feature: Snapshot images can now be automatically flipped vertically if the Flip Image setting is enabled for the camera.
- Added is_flipped field to the database and to the add/edit camera form.
- New support for `user_phone` parameter in snapshot and snapshot file retrieval endpoints:
  - Automatically resolves name from `WhatsappWhitelist` table.
  - Formats audit log as `Name (phone_number)` if name is available.

### Changed
- Snapshot-related audit logs now include user identity in the format `Name (phone_number)` when accessed via WhatsApp Bot.

## [1.5.4] - 2025-07-31

### Added

### Changed
- /cctv/resolve-ip endpoint now not return camera with no IP and list all cameras under group name

### Fixed
- Snapshot Scheduler Log now recorded when scheduled snapshot job running for hundred of cameras.


## [1.5.3] - 2025-07-30

### Added
- Added tampered reason and resolution to maps popup window

### Changed
- Latitude and longitude inputs now support up to 7 decimal digits.
- Grouping API Documentation

### Fixed
- Error when CCTV name contains parentheses is now resolved.
- The "extra" field in audit logs is now displayed correctly.
- Modal popups now appear as expected.
- Long text in map popups no longer overflows.


## [1.5.2] - 2025-07-29

### Changed
- Video Gallery grid column for XL screen from 4 to 3.

### Fixed
- Typo in get audit logs API, cause extra field not return to frontend
- CCTV Hostname overflow in maps

## [1.5.1] - 2025-07-29

### Changed
- PostgreSQL connections are pooled using SQLAlchemy (pool_size=20, max_overflow=10) for better connection reuse and performance.
- Remove support SQLite DB.


## [1.5.0] - 2025-07-28

### Added
- Map: Dynamic Pie Chart Clusters - Cluster markers on the map now display as pie charts, showing the proportion of Online, High Latency, and Offline cameras within that area.
- Map: Pulsing Animation for Online Markers - "Online" camera markers now have a subtle pulse animation for better visibility of live assets.
- Map: Blinking Animation for Offline Markers - "Offline" camera markers now have a blinking animation to draw attention to critical issues.
- Map: Marker Hover Effect - All map markers now scale up slightly on hover for improved user interaction feedback.

### Changed
- Map: Cluster Style Overhaul - Replaced the default cluster markers with the new, more informative pie chart style.
- Map: Marker Aesthetics - Individual camera markers have been restyled with cleaner borders and shadows for a more modern look.


## [1.4.0] - 2025-07-28

### Added
- Separate dropdowns for log type and log file.
- Automatic color highlighting for log levels: ERROR, WARN, INFO, DEBUG.
- Log parsing into timestamp, level, and message sections.
- Auto-scroll to the bottom after displaying log content.
- Download button is only enabled after a file is selected.
- Escaping of HTML in log content to prevent XSS.

### Changed
- Increase Snapshot Log size from 1MB to 5MB
- Log content is no longer loaded automatically on page load.

### Fixed
- Fixed bug camera with "Restricted" status skipped from scheduled snapshots.
- Fixed missing `db.commit()` in `run_snapshot()` which caused snapshot records not being saved to the database.
- Improved scheduled snapshot task reporting:
  - `"success"` if all cameras succeed
  - `"partial"` if some cameras fail
  - `"fail"` only if all snapshots fail


## [1.3.2] - 2025-07-25

### Added
- Early validation combining `session_user_id` and `session_token` before processing the request.
- Explicit logging for each authentication failure scenario (invalid token, corrupt session_user_id, user not found).

### Changed
- Refactored authentication flow to be more defensive and structured:
  - Public paths and query tokens are checked earlier.
  - Session & token validation is performed before falling back.
  - Fallback to cookie-based session only occurs if initial validation fails.
- `session.clear()` is now only called once at the end if all authentication methods fail, and not during internal logic errors or parsing issues.

### Fixed
- Bug where the login session was cleared if `session_user_id` was invalid or when exceptions were raised by external endpoints such as `try_auth()`.
- Prevented unnecessary redirect to login when `session_token` is still valid but wasn't validated early.


## [1.3.1] - 2025-07-25
### Added

### Changed

### Fixed
- **Snapshot:** Fixed bug can't snapshot camera with "Restricted" status..
- **Snapshot Gallery:** Fixed a critical bug where the gallery would stop refreshing after a new filter was applied. This would happen if a previous search resulted in a partial page of images, causing the component to get stuck in a "no more results" state. The gallery filtering and search are now stable and reset correctly with every new request.



## [1.3.0] - 2025-07-25
### Added
- Timezone formatting is now consistent across all menus.
- Tampered snapshots (blurred, occluded, overexposed) now show a badge indicator in the gallery.
- Snapshot scheduler log.

### Changed
- Timestamps now use localized timezone abbreviations (e.g. WITA, +08) instead of raw UTC.

### Fixed
- Fixed inconsistent timestamp displays between snapshot gallery and audit log views.


## [1.2.3] - 2025-07-23
### Added

### Changed

### Fixed
- Delete camera endpoint bug.

## [1.2.2] - 2025-07-22
### Added

### Changed

### Fixed
- Name become '-' when toggle active in whitelist menu.

## [1.2.1] - 2025-07-21
### Added

### Changed
- Bump dependencies.

### Fixed
- Snapshot scheduler not refreshed..


## [1.2.0] - 2025-07-18
### Added
- WhatsApp whitelist admin page with role support.

### Changed

### Fixed

