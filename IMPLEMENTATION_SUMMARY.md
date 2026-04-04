# B-SNAP Security Audit - Implementation Summary

> **Implementation Date:** 27 Maret 2026  
> **Status:** ✅ P0, P1 & P2 COMPLETE

---

## 🎯 Executive Summary

Semua rekomendasi **P0 (Critical)**, **P1 (High)**, dan **P2 (Medium)** telah berhasil diimplementasikan. Sistem B-SNAP sekarang memiliki:

- ✅ **Evidence Integrity:** File hash SHA-256 untuk semua snapshot dan video
- ✅ **Soft Delete:** Data tidak terhapus permanen, dapat di-restore
- ✅ **Password Encryption:** AES-256-GCM untuk kredensial kamera
- ✅ **API Security:** Password tidak muncul di response API
- ✅ **Retention Hold:** Legal hold untuk footage kritis (tidak bisa di-purge)
- ✅ **Safety Classification:** Klasifikasi kamera untuk mining compliance

---

## 📋 Detailed Implementation

### P0-001: File Hash (SHA-256) ✅

**Files Modified:**
- `app/models/snapshot.py` - Added `file_hash` column dan methods
- `app/models/video.py` - Added `file_hash` column dan methods
- `app/utils/snapshot_utils.py` - Hash calculation on save
- `app/utils/video.py` - Hash calculation on video save
- `alembic/versions/20260326_add_file_hash_and_soft_delete.py` - Migration

**Features:**
- Automatic SHA-256 hash calculation saat snapshot/video dibuat
- `verify_integrity()` method untuk memeriksa keaslian file
- API endpoint: `GET /snap/{snapshot_id}/verify`

**Contoh Hash:**
```
File: snapshot_001.jpg
Hash: a3f5c8e2d9b1... (64 karakter hex)
```

---

### P0-002: Soft Delete ✅

**Files Modified:**
- `app/models/snapshot.py` - Added `deleted_at` column, `soft_delete()`, `restore()`
- `app/models/video.py` - Added `deleted_at` column, `soft_delete()`, `restore()`
- `app/utils/snapshot_service.py` - Soft delete logic
- `app/routes/snap_gallery.py` - Soft delete routes, admin purge
- `app/routes/videos.py` - Soft delete for videos
- `alembic/versions/20260326_add_file_hash_and_soft_delete.py` - Migration

**API Endpoints:**
- `DELETE /snap/{id}` - Soft delete snapshot
- `POST /snap/{id}/restore` - Restore soft-deleted snapshot
- `DELETE /snap/{id}?hard_delete=true` - Hard delete (admin only)
- `DELETE /videos/{id}?hard_delete=true` - Hard delete video (admin only)
- `POST /videos/{id}/restore` - Restore soft-deleted video
- `GET /admin/snapshots/deleted` - List deleted snapshots (admin)
- `POST /admin/snapshots/purge` - Purge old deleted snapshots (admin)
- `POST /admin/videos/purge` - Purge old deleted videos (admin)

**Behavior:**
- Delete biasa = soft delete (data tetap ada, flagged as deleted)
- Hard delete hanya bisa oleh admin
- Purge otomatis untuk data yang sudah di-soft-delete > 30 hari

---

### P1-001: Password Encryption (AES-256-GCM) ✅

**Files Modified:**
- `app/utils/encryption.py` - **NEW FILE** - AES-256-GCM encryption utility
- `app/models/camera.py` - Password encryption/decryption property
- `.env.example` - Added `ENCRYPTION_KEY` environment variable

**Encryption Details:**
- Algorithm: AES-256-GCM
- Key Source: Environment variable `ENCRYPTION_KEY`
- Key Derivation: SHA-256 dari ENCRYPTION_KEY
- Nonce: Random 12 bytes per encryption
- Format: `ENC:<base64(nonce + ciphertext)>`

**Contoh:**
```python
# Plain text: "camera123"
# Encrypted: "ENC:-idMJyASsd5ezf7btd8-vueay0..."
```

**Backward Compatibility:**
- Password lama yang belum terenkripsi tetap berfungsi
- Otomatis terenkripsi saat update berikutnya

---

### P1-002: Remove Password from API Response ✅

**Files Modified:**
- `app/routes/cameras.py` - Removed password dari `get_camera_details()`, added `get_camera_password()` admin endpoint

**API Changes:**
- `GET /api/camera/{id}` - **NO LONGER includes password**
- `GET /api/camera/{id}/password` - **NEW** Admin only, logged access

**Audit Logging:**
- Setiap akses password dicatat di audit log
- User, IP, timestamp, dan camera ID tercatat

---

### P1-003: NVR Password Encryption & API Security ✅

**Files Modified:**
- `app/models/nvr.py` - Added AES-256 encryption for NVR passwords (same as Camera)
- `app/routes/nvrs.py` - Removed password dari API response, added `get_nvr_password()` admin endpoint
- `templates/nvrs.html` - Updated to use icons from `_icons.html`, fetch password from separate endpoint
- `alembic/versions/20260326_encrypt_nvr_password.py` - Migration for password column

**API Changes:**
- `GET /api/nvr/{id}` - **NO LONGER includes password**
- `GET /api/nvr/{id}/password` - **NEW** Admin only, logged access

**Frontend Changes:**
- Using `icon_eye` and `icon_eye_slash` from `_icons.html`
- Fixed toggle button alignment
- Fetch decrypted password from admin endpoint

---

## 🗄️ Database Changes

### New Columns

| Table | Column | Type | Purpose |
|-------|--------|------|---------|
| snapshots | file_hash | VARCHAR(64) | SHA-256 hash untuk integritas |
| snapshots | deleted_at | TIMESTAMP | Soft delete timestamp |
| videos | file_hash | VARCHAR(64) | SHA-256 hash untuk integritas |
| videos | deleted_at | TIMESTAMP | Soft delete timestamp |

### Modified Columns

| Table | Column | Change |
|-------|--------|--------|
| cameras | password | VARCHAR(255) → TEXT (untuk encrypted data) |
| nvr | password | VARCHAR(100) → TEXT (untuk encrypted data) |

---

## 🔧 Environment Variables

Tambahkan ke file `.env`:

```bash
# Encryption key untuk password camera (generate dengan: openssl rand -hex 32)
ENCRYPTION_KEY=your-32-byte-hex-encryption-key-here
```

⚠️ **IMPORTANT:** Backup encryption key! Jika hilang, password camera tidak bisa didekripsi.

---

## 🧪 Testing

### Manual Test Commands

```bash
# 1. Test file hash
curl http://localhost:8000/snap/{snapshot_id}/verify

# 2. Test soft delete
curl -X DELETE http://localhost:8000/snap/{snapshot_id}

# 3. Test restore
curl -X POST http://localhost:8000/snap/{snapshot_id}/restore

# 4. Test camera API (no password)
curl http://localhost:8000/api/camera/{camera_id}

# 5. Test admin password endpoint (requires admin)
curl http://localhost:8000/api/camera/{camera_id}/password

# 6. List deleted snapshots (admin only)
curl http://localhost:8000/admin/snapshots/deleted
```

---

## 📊 Migration Status

```
Revision: 20260326_add_file_hash_and_soft_delete.py
Status: ✅ Applied
Database: PostgreSQL (production)
```

Commands:
```bash
# Check status
alembic current

# Upgrade (if needed)
alembic upgrade head

# Downgrade (rollback)
alembic downgrade -1
```

---

## ✅ Acceptance Criteria Verification

### P0 Criteria
- [x] All new snapshots have SHA-256 hash
- [x] All new videos have SHA-256 hash
- [x] Delete sets `deleted_at` timestamp
- [x] Gallery filters out soft-deleted items
- [x] Admin can view deleted items
- [x] Admin can hard delete if needed

### P1 Criteria
- [x] New camera passwords encrypted with AES-256
- [x] Existing plain-text passwords still work
- [x] Camera API doesn't include password
- [x] Admin-only endpoint for password retrieval
- [x] Encryption key from environment variable

---

## 🚀 Next Steps (P2/P3)

### P2 - MEDIUM
1. ✅ **Append-Only Audit Log** - Database triggers mencegah UPDATE/DELETE
2. ✅ **Safety Classification** - Field untuk klasifikasi kamera (critical/standard)
3. ✅ **Retention Hold** - Legal hold untuk footage incident
4. ✅ **Static File Protection** - Serve snapshots hanya via API dengan autentikasi

### P3 - LOW (Outstanding)
1. **Cookie Security** - Enforce secure cookies in production
2. **Blockchain Anchoring** - Future consideration for audit log integrity

---

### P2-001: Append-Only Audit Log ✅

**Files Modified:**
- `app/models/audit_log.py` - Model dengan PostgreSQL triggers
- `app/utils/audit_logger.py` - Enhanced logging dengan request details
- `alembic/versions/a0b22741533a_add_p2_001_append_only_audit_log.py`

**Database Enforcement:**
```sql
-- Triggers mencegah UPDATE/DELETE:
CREATE TRIGGER audit_log_prevent_update BEFORE UPDATE ON audit_logs
CREATE TRIGGER audit_log_prevent_delete BEFORE DELETE ON audit_logs
```

**New Columns:**
| Column | Type | Description |
|--------|------|-------------|
| user_agent | VARCHAR(500) | HTTP User-Agent |
| request_path | VARCHAR(500) | API endpoint |
| request_method | VARCHAR(10) | HTTP method |
| response_status | INTEGER | HTTP status code |

**Functions:**
- `log_audit()` - Basic audit logging
- `log_api_access()` - Full API access logging

---

### P2-002: Safety Classification ✅

**Files Modified:**
- `app/models/camera.py` - Added `safety_classification` column
- `app/schemas/camera.py` - Added to schema
- `app/routes/cameras.py` - CRUD support
- `templates/cameras.html` - Dropdown in form
- `alembic/versions/8a53b8bab616_add_safety_classification_p2_002.py`

**Values:**
- `critical` - Incident coverage cameras
- `standard` - Regular monitoring (default)
- `low` - General surveillance

---

### P2-003: Retention Hold ✅

**Files Modified:**
- `app/models/snapshot.py` - Added retention hold columns
- `app/models/video.py` - Added retention hold columns
- `app/routes/snap_gallery.py` - Retention hold endpoints
- `app/routes/videos.py` - Retention hold endpoints
- `templates/admin_trash.html` - UI for retention hold
- `alembic/versions/f3170d221195_add_retention_hold_p2_001.py`

**Database Columns:**
| Column | Type | Description |
|--------|------|-------------|
| retention_hold | BOOLEAN | Flag untuk legal hold |
| retention_hold_reason | VARCHAR(500) | Alasan hold |
| retention_hold_by | VARCHAR(100) | User yang apply hold |
| retention_hold_at | TIMESTAMP | Waktu hold diterapkan |

**API Endpoints:**
- `POST /snap/{id}/retention-hold?enable=true&reason=...` - Apply/remove hold
- `POST /videos/{id}/retention-hold?enable=true&reason=...` - Apply/remove hold

**Behavior:**
- Items dengan retention hold tidak bisa di-purge
- Bulk purge otomatis skip retention hold items
- Badge visual di Trash Management UI

---

### P2-004: Secure Snapshot Serving ✅

**Files Modified:**
- `app/main.py` - Blocking route `/static/snapshots/{path:path}` → 403 Forbidden
- `app/routes/snap_gallery.py` - Authenticated API endpoints + gallery view optimization
- `templates/_gallery_grid.html` - Uses secure API URLs dengan thumb parameter
- `templates/snapshot_gallery.html` - Batch logging untuk gallery view
- `templates/admin_trash.html` - Updated image URLs

**Security Changes:**
- Blocked: Direct access ke `/static/snapshots/*` → 403 Forbidden
- Required: Authentication via API endpoints
- Gallery: Menggunakan `/api/snapshots/secure/{id}?thumb=true` (minimal logging)
- Detail View: Menggunakan `/api/snapshots/secure/{id}` (full logging)

**Audit Log Strategy (Anti-Flooding):**
| Action | Endpoint | Logging |
|--------|----------|---------|
| Gallery Browse | `POST /api/snapshots/gallery-view` | **Batch** - 1 log per page |
| Thumbnail Load | `GET /api/snapshots/secure/{id}?thumb=true` | Minimal - `gallery_thumbnail` |
| Detail View | `GET /api/snapshots/secure/{id}` | Full - `view_snapshot` |
| Download | `GET /api/snapshots/secure/{id}?download=true` | Full - `download_snapshot` |

**API Endpoints:**
| Endpoint | Description |
|----------|-------------|
| `GET /static/snapshots/{path}` | **BLOCKED** - Returns 403 Forbidden |
| `POST /api/snapshots/gallery-view` | Batch log gallery page view |
| `GET /api/snapshots/file/{file_path}` | Access by file path (authenticated) |
| `GET /api/snapshots/secure/{snapshot_id}?thumb=true` | Gallery thumbnail (minimal log) |
| `GET /api/snapshots/secure/{snapshot_id}` | Detail view (full log) |
| `GET /api/snapshots/secure/{id}?download=true` | Download dengan filename |

**Features:**
- Route blocking di app level (sebelum StaticFiles)
- **Anti-flooding**: Gallery view log sekali per page, bukan per gambar
- Directory traversal protection
- Authentication required (Operator/Admin)
- Audit logging dengan konteks (gallery vs detail)
- Download dengan filename: `CameraName_YYYYMMDD_HHMMSS.jpg`
- Soft-deleted snapshot protection (admin only)

---

## 📁 Files Created/Modified

### New Files
- `app/utils/encryption.py` - AES-256 encryption utility
- `alembic/versions/20260326_add_file_hash_and_soft_delete.py` - Migration
- `alembic/versions/5cedbb9e3357_merge_heads.py` - Migration merge

### Modified Files
- `app/models/snapshot.py` - Hash & soft delete columns
- `app/models/video.py` - Hash & soft delete columns
- `app/models/camera.py` - Password encryption
- `app/models/nvr.py` - Password encryption
- `app/utils/snapshot_utils.py` - Hash calculation
- `app/utils/snapshot_service.py` - Soft delete logic
- `app/utils/video.py` - Hash calculation
- `app/routes/snap_gallery.py` - Soft delete routes
- `app/routes/videos.py` - Soft delete routes
- `app/routes/cameras.py` - Remove password dari API
- `app/routes/nvrs.py` - Remove password dari API
- `templates/nvrs.html` - Password toggle icons & fetch
- `.env.example` - ENCRYPTION_KEY variable

---

## 🎉 Summary

**Status:** ✅ **P0, P1 & P2 COMPLETE**

Semua critical, high, dan medium priority findings telah diperbaiki:

**P0 (Critical):**
1. ✅ **Evidence Integrity** - File hash SHA-256
2. ✅ **Tamper Protection** - Soft delete, tidak ada permanent deletion tanpa izin

**P1 (High):**
3. ✅ **Password Security** - AES-256 encryption
4. ✅ **API Security** - Password tidak exposed

**P2 (Medium):**
5. ✅ **Append-Only Audit Log** - Database triggers mencegah tampering
6. ✅ **Safety Classification** - Klasifikasi kamera untuk mining compliance
7. ✅ **Retention Hold** - Legal hold untuk footage kritis
8. ✅ **Secure File Access** - Snapshots hanya via API dengan autentikasi

Sistem sekarang **SIAP UNTUK PRODUCTION** dengan tingkat keamanan yang sesuai untuk mining operations.

---

**Implemented by:** AI Security Engineer  
**Date:** 26 Maret 2026  
**Review Status:** Pending QA
