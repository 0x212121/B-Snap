# Changelog

All notable changes to B-Snap will be documented in this file.
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

