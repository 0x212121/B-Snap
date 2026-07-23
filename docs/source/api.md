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

**Notes**:
- Direct `/static/videos/*` access is blocked by design.
- Newly recorded videos use this endpoint in WebSocket `record_complete` notifications through the `video_url` field.
- Thumbnail previews use `GET /api/videos/secure/{video_id}?thumb=true`.

---

### Get Video by File Path

```text
GET /api/videos/file/{file_path}
```

**Security**: Direct file path access with authentication

---

### Start Video Recording

Trigger an asynchronous video recording for a camera.

```text
POST /videos/record/{camera_id}?duration=10
```

**Parameters**:
- `camera_id` (path, required): UUID of the camera
- `duration` (query, optional): Recording duration in seconds, allowed range `5` to `60`

**Response**:
- `202 Accepted` - Recording started

When the recording completes, B-Snap emits a WebSocket `record_complete` message containing:

```json
{
  "type": "record_complete",
  "video_id": "video-uuid",
  "video_url": "/api/videos/secure/video-uuid",
  "thumb_url": "/api/videos/secure/video-uuid?thumb=true",
  "camera_name": "Camera-01",
  "group": "Plant-A",
  "duration": 10,
  "file_size": 1234567
}
```

Metadata extraction uses `ffprobe` when available and falls back to `ffmpeg` output parsing when `ffprobe` is unavailable. Thumbnail generation uses `ffmpeg` or the project-local `ffmpeg.exe`.

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

## Record Checks API

Record checks monitor mounted SMB/NVR recording folders. All endpoints require admin access.

### List Sources

```text
GET /api/record-checks/sources
```

### Create or Update Source

```text
POST /api/record-checks/sources
PUT /api/record-checks/sources/{source_id}
```

Sources should point to an absolute mounted path. Mount SMB shares read-only on the host/container, then configure the mounted path in B-SNAP.

### Delete Source

```text
DELETE /api/record-checks/sources/{source_id}
```

Delete is protected by:

- current admin password
- confirmation name matching the linked NVR hostname, or source name when no NVR is linked
- audit logging

### Run Check Now

```text
POST /api/record-checks/sources/{source_id}/run
```

### Current Status and History

```text
GET /api/record-checks/status
GET /api/record-checks/runs
```

Statuses include `healthy`, `stale`, `long_dead`, `unknown`, and `missing`. Previously known folders that disappear are retained as `missing` so operators can investigate renamed or deleted folders.

### Power BI Record Check Endpoints

These read-only endpoints are designed for Power BI Web connector usage through an on-premises Power BI Gateway. Use a B-SNAP API token in the `Authorization` header:

```text
Authorization: Bearer <api_token>
```

Available datasets:

```text
GET /api/powerbi/record-checks/sources
GET /api/powerbi/record-checks/current-status
GET /api/powerbi/record-checks/runs
GET /api/powerbi/record-checks/folder-checks
GET /api/powerbi/record-checks/events
GET /api/powerbi/record-checks/downtime
```

All endpoints return flat JSON rows under `data`. Paginated endpoints also return `pagination` with `limit`, `offset`, `count`, `total`, `has_more`, and `next_offset`.

Common parameters:

- `limit`: 1-5000 rows per request, default 1000
- `offset`: zero-based offset for pagination
- `source_id`: optional source filter
- date filters use ISO-8601 timestamps, for example `2026-07-01T00:00:00+08:00`

Endpoint-specific filters:

- `current-status`: `status`, `updated_since`
- `runs`: `status`, `started_from`, `started_to`
- `folder-checks`: `run_id`, `status`, `checked_from`, `checked_to`
- `events`: `event_type`, `created_from`, `created_to`
- `downtime`: required `start_at` and `end_at`, optional `source_id`, optional `group_by=channel|interval`

Use `/api/powerbi/record-checks/downtime` to calculate how long a channel was not recording in a selected time range. The default `group_by=channel` returns dashboard-ready rows with folder/channel name, camera name, current status, downtime history, incident count, and duration. `group_by=interval` returns each problem/recovery interval.

Power Query example:

```powerquery
let
    BaseUrl = "http://bsnap.local:8080",
    Token = "YOUR_API_TOKEN",
    GetPage = (Offset as number) =>
        Json.Document(
            Web.Contents(
                BaseUrl,
                [
                    RelativePath = "api/powerbi/record-checks/current-status",
                    Query = [limit = "1000", offset = Text.From(Offset)],
                    Headers = [Authorization = "Bearer " & Token]
                ]
            )
        ),
    Pages = List.Generate(
        () => GetPage(0),
        each [pagination][count] > 0,
        each if [pagination][has_more] then GetPage([pagination][next_offset]) else [pagination = [count = 0]],
        each [data]
    ),
    Rows = List.Combine(Pages),
    Table = Table.FromRecords(Rows)
in
    Table
```

### Camera Mapping

```text
GET /api/record-checks/cameras
PUT /api/record-checks/status/{status_id}/mapping
```

Manual folder-to-camera mappings take precedence over automatic hostname matching.

---

## Job Management API

Job Management lists all configured scheduler jobs, including jobs that are configured but waiting for scheduler reload.

```text
GET /admin/jobs/api/jobs
GET /admin/jobs/api/jobs/{job_id}/history
POST /admin/jobs/api/jobs/{job_id}/schedule
```

Important record-check jobs:

- `record_folder_check`: scans mounted recording folders and sends stale/missing/recovery alerts.
- `cleanup_record_checks`: hard-deletes old record-check run/check/event history based on `retention_record_check_days`.

Each job response includes a description field used by the UI tooltip.

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

### Check GoWA Status

```text
GET /api/wa/status
```

Admin-only endpoint for checking the configured GoWA gateway. Optional query overrides can be passed by the config UI before saving:

- `enabled`
- `base_url`
- `api_key`

### List GoWA Groups

```text
GET /api/wa/groups
```

Admin-only endpoint that loads WhatsApp groups from GoWA using `GET /user/my/groups`. The response is used by the config UI to append group JIDs such as `120363xxxxxxxx@g.us` to default receivers.

### Send Test Message

```text
POST /api/wa/send-test
```

Admin-only endpoint for sending a test WhatsApp message through GoWA.

### Webhook

```text
POST /webhook/gowa
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
