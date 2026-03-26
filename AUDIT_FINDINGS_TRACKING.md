# B-SNAP Audit Findings Tracking

> **Dokumen Tracking untuk Perbaikan Security Audit**  
> **Dibuat:** 26 Maret 2026  
> **Status:** Active Remediation  

---

## 📊 Ringkasan Status

| Category | Total | Outstanding | In Progress | Done |
|----------|-------|-------------|-------------|------|
| Critical (P0) | 2 | 0 | 0 | 2 |
| High (P1) | 3 | 0 | 0 | 3 |
| Medium (P2) | 4 | 4 | 0 | 0 |
| Low (P3) | 1 | 1 | 0 | 0 |
| **TOTAL** | **10** | **5** | **0** | **5** |

**Overall Progress:** 100% P0/P1 Complete | 50% Overall  
**Overall Status:** 🟢 **P0/P1 COMPLETE - Ready for Production**

---

## 🔴 P0 - CRITICAL (Immediate - 0-30 days)

### [x] P0-001: Implement File Hash (SHA-256) for Snapshots/Videos
- **Finding:** No cryptographic hash for evidence integrity
- **Status:** ✅ **DONE**
- **Implemented:** 
  - Added `file_hash` column to `snapshots` table
  - Added `file_hash` column to `videos` table
  - Hash calculated on save using SHA-256
  - Verification endpoint for integrity checks
- **Files Modified:**
  - `app/models/snapshot.py`
  - `app/models/video.py`
  - `app/utils/snapshot_service.py`
  - `app/utils/video.py`
  - Alembic migration file
- **Tested:** Hash generation and verification working
- **Completed Date:** 2026-03-26

### [x] P0-002: Add Soft Delete (deleted_at) for Snapshots/Videos
- **Finding:** Footage can be permanently deleted without trace
- **Status:** ✅ **DONE**
- **Implemented:**
  - Added `deleted_at` column to `snapshots` table
  - Added `deleted_at` column to `videos` table
  - Modified delete operations to soft delete only
  - Added `is_deleted` property for filtering
  - Admin-only purge function for actual deletion (with audit log)
- **Files Modified:**
  - `app/models/snapshot.py`
  - `app/models/video.py`
  - `app/routes/snap_gallery.py`
  - `app/routes/videos.py`
  - `app/utils/snapshot_service.py`
  - Alembic migration file
- **Tested:** Soft delete preserves data, admin purge works
- **Completed Date:** 2026-03-26

---

## 🟠 P1 - HIGH (Short-term - 1-3 months)

### [x] P1-001: Encrypt Camera Passwords with AES-256
- **Finding:** Camera credentials stored in plain text
- **Status:** ✅ **DONE**
- **Implemented:**
  - Created `app/utils/encryption.py` with AES-256-GCM
  - Encryption key from environment variable `ENCRYPTION_KEY`
  - Automatic encryption on password save
  - Automatic decryption on password read
  - Backward compatibility with existing plain-text passwords
- **Files Modified:**
  - `app/models/camera.py` (password encryption/decryption)
  - `app/utils/encryption.py` (new file)
  - `.env.example` (added ENCRYPTION_KEY)
  - Alembic migration file (for password field length)
- **Tested:** Encryption/decryption working, existing passwords migrated
- **Completed Date:** 2026-03-26

### [x] P1-002: Remove Password from Camera API Response
- **Finding:** Camera password exposed in API responses
- **Status:** ✅ **DONE**
- **Implemented:**
  - Modified `/api/camera/{camera_id}` endpoint
  - Password field excluded from JSON response
  - Created separate admin-only endpoint for password viewing
  - Frontend updated to not expect password in response
- **Files Modified:**
  - `app/routes/cameras.py` (API response)
  - `app/routes/auth.py` (admin endpoint)
  - Template files if needed
- **Tested:** Password no longer visible in API responses
- **Completed Date:** 2026-03-26

### [x] P1-003: NVR Password Encryption & API Security
- **Finding:** NVR passwords stored in plain text and exposed in API
- **Status:** ✅ **DONE**
- **Implemented:**
  - AES-256-GCM encryption for NVR passwords (same as Camera)
  - Updated `app/models/nvr.py` with encrypted password property
  - New admin endpoint: `GET /api/nvr/{id}/password`
  - Password excluded from standard NVR API responses
  - Updated `templates/nvrs.html` with proper password toggle icons
  - Migration: `20260326_encrypt_nvr_password.py`
- **Files Modified:**
  - `app/models/nvr.py` (password encryption)
  - `app/routes/nvrs.py` (API security)
  - `templates/nvrs.html` (icon integration)
  - Alembic migration
- **Tested:** Encryption working, password not exposed in API
- **Completed Date:** 2026-03-26

---

## 🟡 P2 - MEDIUM (1-3 months)

### [ ] P2-001: Implement Append-Only Audit Log
- **Finding:** Audit logs can be modified or deleted
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Database triggers preventing UPDATE/DELETE on audit_logs
  - Separate immutable audit table with WORM enforcement
  - Optional: External SIEM integration
- **Files to Modify:**
  - Database triggers (PostgreSQL)
  - `app/models/audit_log.py`
  - Alembic migration
- **Assigned To:** TBD
- **Target Date:** TBD

### [ ] P2-002: Add Safety Classification to Cameras
- **Finding:** No distinction between safety-critical and general cameras
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Add `safety_classification` column (critical/standard/monitoring)
  - UI updates for classification selection
  - Filter views by safety classification
  - Priority alerts for critical camera downtime
- **Files to Modify:**
  - `app/models/camera.py`
  - `app/routes/cameras.py`
  - Templates: `cameras.html`, forms
  - Alembic migration
- **Assigned To:** TBD
- **Target Date:** TBD

### [ ] P2-003: Implement Retention Hold for Incident Footage
- **Finding:** No legal hold capability for incident-related footage
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Add `retention_hold` flag to snapshots/videos
  - Add `hold_reason` and `hold_expires_at` columns
  - Prevent deletion of held footage
  - UI for applying/removing holds
- **Files to Modify:**
  - `app/models/snapshot.py`, `video.py`
  - `app/routes/snap_gallery.py`, `videos.py`
  - Templates
  - Alembic migration
- **Assigned To:** TBD
- **Target Date:** TBD

### [ ] P2-004: Move Snapshots Outside Web Root
- **Finding:** Direct static file access bypasses audit logging
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Serve all snapshots through authenticated API only
  - Remove `/static/snapshots` from StaticFiles mount
  - Implement streaming response for images
  - Update all image URLs in templates
- **Files to Modify:**
  - `app/main.py` (StaticFiles configuration)
  - `app/routes/snapshots.py` (new streaming endpoint)
  - All templates using snapshot URLs
- **Assigned To:** TBD
- **Target Date:** TBD

---

## 🟢 P3 - LOW (3-6 months)

### [ ] P3-001: Session Cookie Security Hardening
- **Finding:** Cookie secure=False in some paths
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Enforce `secure=True` for all cookies in production
  - Add `SameSite=Strict` for sensitive cookies
  - Verify HTTPS enforcement
- **Files to Modify:**
  - `app/routes/auth.py`
  - `app/middleware/auth_and_setup.py`
- **Assigned To:** TBD
- **Target Date:** TBD

### [ ] P3-002: Blockchain Anchoring for Audit Logs
- **Finding:** No cryptographic proof of audit log integrity
- **Status:** 🔲 **OUTSTANDING**
- **Planned Implementation:**
  - Periodic batch hashing of audit logs
  - Anchoring to blockchain or Merkle tree
  - Verification mechanism
- **Files to Modify:**
  - New module: `app/utils/blockchain_anchor.py`
  - Scheduled job for anchoring
- **Assigned To:** TBD
- **Target Date:** TBD (Future consideration)

---

## 📋 Acceptance Criteria

### P0 Acceptance Criteria
- [x] All new snapshots have SHA-256 hash stored in database
- [x] All new videos have SHA-256 hash stored in database
- [x] Deleting snapshot sets `deleted_at` timestamp instead of removing record
- [x] Deleting video sets `deleted_at` timestamp instead of removing record
- [x] Gallery views filter out soft-deleted items
- [x] Admin can view deleted items and purge if needed

### P1 Acceptance Criteria
- [x] New camera passwords are encrypted with AES-256
- [x] Existing plain-text passwords still work (backward compatibility)
- [x] Camera API endpoint does not include password in response
- [x] Admin-only endpoint available for password retrieval when needed
- [x] Encryption key stored securely in environment variable

### P2 Acceptance Criteria (Pending)
- [ ] Audit logs table has triggers preventing UPDATE/DELETE
- [ ] Cameras have safety_classification field
- [ ] Snapshots/videos have retention_hold fields
- [ ] Snapshots served only through authenticated API

---

## 📝 Changelog

| Date | Item | Status | Notes |
|------|------|--------|-------|
| 2026-03-26 | P0-001 | Done | File hash implementation complete |
| 2026-03-26 | P0-002 | Done | Soft delete implementation complete |
| 2026-03-26 | P1-001 | Done | Password encryption complete |
| 2026-03-26 | P1-002 | Done | Password removed from API response |
| 2026-03-26 | P1-003 | Done | NVR password encryption & API security |
| 2026-03-26 | CHANGELOG | Done | Security updates documented in CHANGELOG.md |

---

## 🔐 Security Verification Checklist

Before marking as complete, verify:

- [x] P0-001: Hash verification passes for test snapshots
- [x] P0-002: Soft-deleted snapshots not visible in gallery
- [x] P1-001: Password encrypted in database (not readable)
- [x] P1-002: API response does not contain password field
- [ ] Database backups exclude encryption keys
- [ ] Audit log integrity verified

---

**Document Owner:** Security Team  
**Last Updated:** 2026-03-26  
**Next Review:** Upon P2 completion
