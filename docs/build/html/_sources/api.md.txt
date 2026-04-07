# API Documentation

## Overview

B-Snap provides a REST API for programmatic access to snapshots, videos, and system functions.

**Base URL**: `http://localhost:8080`  
**Authentication**: Bearer Token  
**Content-Type**: `application/json`

---

## Authentication

### Bearer Token

Include the API token in the Authorization header:

```
Authorization: Bearer your-api-token
```

### Obtaining API Tokens

API tokens are generated via the admin interface:

1. Login as admin
2. Navigate to User Management
3. Select user and click "Generate API Token"
4. Set expiration (in days) - MED-002 requires expiration

---

## Snapshots API

### Get Snapshot File

Retrieve a snapshot file by ID with authentication.

```text
GET /api/snapshots/secure/{snapshot_id}
```

**Parameters**:
- `snapshot_id` (path, required): UUID of the snapshot
- `thumb` (query, optional): `true` for minimal logging (gallery view)
- `download` (query, optional): `true` to force download

**Responses**:
- `200 OK` - Image file
- `401 Unauthorized` - Missing or invalid token
- `403 Forbidden` - Access denied to deleted snapshot
- `404 Not Found` - Snapshot not found

**Example**:
```text
curl -H "Authorization: Bearer your-token" \
  http://localhost:8080/api/snapshots/secure/abc-123
```

---

### Get Snapshot by File Path

Retrieve snapshot using file path.

```text
GET /api/snapshots/file/{file_path}
```

**Parameters**:
- `file_path` (path, required): Relative file path

**Security**: CRIT-001 - Replaces direct static access

---

### Log Gallery View

Batch logging for gallery view (anti-flooding).

```text
POST /api/snapshots/gallery-view
```

**Body**:
```json
{
  "camera_filter": "Camera Name",
  "snapshot_count": 15,
  "search_query": "search term"
}
```

**Purpose**: Log once per page load instead of per image

---

## Videos API

### Get Video File

Retrieve a video file by ID with authentication.

```text
GET /api/videos/secure/{video_id}
```

**Parameters**:
- `video_id` (path, required): UUID of the video
- `thumb` (query, optional): `true` for thumbnail view
- `download` (query, optional): `true` to force download

**Responses**:
- `200 OK` - Video file
- `401 Unauthorized` - Missing or invalid token
- `403 Forbidden` - Access denied
- `404 Not Found` - Video not found

**Example**:
```text
curl -H "Authorization: Bearer your-token" \
  http://localhost:8080/api/videos/secure/abc-123
```

**Security**: CRIT-001 - Authenticated video access

---

### Get Video by File Path

```text
GET /api/videos/file/{file_path}
```

**Security**: Direct file path access with authentication

---

### Log Video Gallery View

```text
POST /api/videos/gallery-view
```

**Body**:
```json
{
  "camera_filter": "Camera Name",
  "video_count": 12,
  "search_query": "search term"
}
```

---

## Camera API

### List Cameras

```text
GET /api/camera_groups
```

**Response**: List of all camera groups

---

### Get Camera Details

```text
GET /api/camera/{camera_id}
```

**Response**: Camera details (excluding password)

---

### Get Camera Password (Admin Only)

```text
GET /api/camera/{camera_id}/password
```

**Security**: Admin only, logged to audit trail

---

## Snapshot Operations

### Create Snapshot

```text
POST /snap/{camera_identifier}
```

**Parameters**:
- `camera_identifier` (path): Camera ID, IP, or hostname
- `user_phone` (query, optional): For WhatsApp integration

**Response**:
```json
{
  "status": "success",
  "file_path": "camera_id/20260115/120000.jpg",
  "snapshot_id": "uuid",
  "resolution": "1920x1080"
}
```

---

### Delete Snapshot

```text
DELETE /snap/{snapshot_id}
```

**Note**: Performs soft delete (P0-002)

---

### Restore Snapshot

```text
POST /snap/{snapshot_id}/restore
```

**Note**: Restore soft-deleted snapshot

---

### Verify Integrity

```text
GET /snap/{snapshot_id}/verify
```

**Response**:
```json
{
  "status": "success",
  "integrity_verified": true,
  "stored_hash": "sha256_hash"
}
```

**Security**: P0-001 - SHA-256 integrity verification

---

## Retention Hold API

### Apply Retention Hold

```text
POST /snap/{snapshot_id}/retention-hold?enable=true&reason=Legal case #123
```

**Security**: P2-003 - Legal hold for critical evidence

---

### Remove Retention Hold

```text
POST /snap/{snapshot_id}/retention-hold?enable=false
```

---

## Trash Management (Admin)

### List Deleted Snapshots

```text
GET /admin/snapshots/deleted?page=1&limit=20
```

---

### Purge Snapshot (Permanent Delete)

```text
DELETE /admin/snapshots/{snapshot_id}/purge
```

**Note**: Skips items with retention_hold=True

---

### Bulk Purge

```text
POST /admin/snapshots/purge?days_old=30&skip_retention_hold=true
```

---

## Audit API

### Query Audit Logs

```text
GET /api/audit-logs?start_date=2026-01-01&end_date=2026-01-31
```

**Security**: P2-001 - Append-only audit logs

---

## User Management (Admin)

### List Users

```text
GET /api/users?page=1&limit=20
```

---

### Create User

```text
POST /api/users
```

**Body**:
```json
{
  "username": "newuser",
  "password": "securepassword",
  "role": "viewer",
  "group_id": 1
}
```

---

### Generate API Token

```text
POST /api/users/generate-token
```

**Body**:
```json
{
  "user_id": 1,
  "expires_in_days": 30
}
```

**Note**: MED-002 requires expires_in_days > 0

---

## Email Templates

### Get Template

```text
GET /api/email-templates/{template_type}
```

**Types**: `tamper_alert`, `recovery_alert`, `offline_alert`

---

### Save Template

```text
POST /api/email-templates/{template_type}/save
```

**Body**:
```json
{
  "subject": "Custom Subject",
  "plain_body": "Plain text body",
  "html_body": "<p>HTML body</p>"
}
```

---

## WhatsApp Integration

### Webhook

```text
POST /wa/webhook
```

**Purpose**: Receive WhatsApp incoming messages

---

## Error Responses

### Standard Error Format

```json
{
  "status": "error",
  "detail": "Error description",
  "code": "ERROR_CODE"
}
```

### Common Status Codes

| Code | Status | Description |
|------|--------|-------------|
| 400 | Bad Request | Invalid parameters |
| 401 | Unauthorized | Missing/invalid token |
| 403 | Forbidden | Access denied |
| 404 | Not Found | Resource not found |
| 500 | Server Error | Internal error |

---

## Security Considerations

### CRIT-001: Secure Media Access

All media access (snapshots/videos) requires:
- Valid authentication token
- Audit logging
- No direct static file access

### MED-002: Token Expiration

API tokens must have expiration:
- Tokens without expiration are rejected (401)
- Expired tokens return clear error message

### Rate Limiting

Consider implementing rate limiting for:
- `/snap/{camera_id}` - Snapshot creation
- `/api/snapshots/secure/{id}` - Media access

---

## See Also

- {doc}`configuration` - Environment variables
- {doc}`security` - Security best practices
- OpenAPI Spec: `/docs/openapi.json`
