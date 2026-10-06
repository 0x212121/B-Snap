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

### Configurable WhatsApp Command Responses

The system Ping host action reuses the existing `GET /ping?ip={host}` handler
in a worker thread. Its reply text matches the API, including latency formatting.
Templates retain `{{target}}`, `{{result}}`, and `steps.ping.body.target`,
`replies`, and `online`; `{{steps.ping.body.result}}` contains the raw API text.
All timeouts expose `steps.error.status_code = 504`; ping execution errors use 502.

Native camera commands distinguish permission failures (403) from missing cameras
or saved snapshots (404). Camera edits and target group changes follow the sender's
whitelist group. Fallback templates can show `{{steps.error.body.message}}` and
`{{steps.error.status_code}}` for the actual failure reason.

Bot Builder's Execute action test calls `POST /api/admin/wa-bot/test-action`
requiring a whitelisted `sender` phone number. Native actions use that sender's
whitelist role and camera group, matching live WhatsApp execution. Admin login
is required to access the test endpoint but does not bypass sender restrictions.
API-flow tests also require a whitelisted sender; API calls retain the selected
API token's permissions. Missing/unlisted senders or insufficient command roles
return HTTP 403 with `steps.error` before executing any action.
It executes the unsaved native action and
returns JSON `status`, `steps`, input `params`, `response_preview`, and failure
previews without sending WhatsApp messages. Capture, camera-edit, and whitelist
tests really perform their actions; API callers must explicitly set `execute: true`.
Success fields use the existing action-specific result nodes. Every native failure
also exposes `{{steps.error.status_code}}`, `{{steps.error.body.status}}`, and
`{{steps.error.body.message}}` in fallback messages. Tests show selectable fields
even on failure. Missing camera, offline camera, and failed capture return
404, 503, and 502 respectively for Capture new snapshot.

`POST /api/snapshots/capture/{identifier}` (or `/snap/{identifier}`) captures a
new snapshot using operator/admin authentication and camera group restrictions.
Success returns `status`, `success`, `message`, `hostname`, `snapshot_id`, and
the protected `snapshot_url`; failures return JSON `status: error`, `success: false`,
`message`, and `detail`, with a non-success HTTP status.

`processing_delay_seconds` controls when processing messages appear (0–60 seconds).
New Bot Builder commands default to three seconds; existing settings default to
zero. Processing does not block command execution and is canceled if success or
failure is ready before the delay expires. Remaining processing messages are
suppressed before fallback delivery. Messages already delivered to WhatsApp
cannot be withdrawn by this setting.

Command inputs are available before any API completes. Set required parameters
to `hostname, duration` and invoke `/record CCTV-GATE-01 30`. Processing messages
can use `Recording {{params.hostname}} for {{duration}}s`. The explicit forms
`{{params.hostname}}` and `{{params.duration}}` and short named aliases work in
API paths/query/JSON bodies and message templates. For example, use JSON body
`{"hostname":"{{params.hostname}}","duration":"{{params.duration}}"}` with the
recording endpoint. A single parameter receives the entire argument; with
multiple parameters, the last receives the remaining text. Run test uses the
unsaved required-parameter names and previews bound inputs. API result variables
such as `{{steps.data.body.hostname}}` only exist after that node responds.

Failed API Flow nodes retain their `status_code` and JSON `body`. Run test shows
these fields as selectable variables even when the API returns an error.
For a node named `data`, a fallback message can use
`HTTP {{steps.data.status_code}}: {{steps.data.body.detail}}` and
`{{steps.data.body.status}}` when supplied by the endpoint. Completed earlier
node results also remain available. The flow stops at the failed node and uses
failure/fallback messages instead of the success response.

Every Bot Builder command action offers Text, Image, and Video response types.
For Image, set `response_image_source` to an HTTP(S) image URL or a protected
snapshot URL, including action/API templates such as
`{{steps.result.body.0.url}}` or `{{steps.data.body.image_url}}`.
The command's rendered response becomes the optional image caption.
For Video, set `response_video_source` to a protected video ID/URL or a result
template. Protected videos require an operator/admin API token; protected images
check the API token for API Flow commands and whitelist group access for native
commands. Missing/deleted files, invalid sources, and denied access produce a
failure response. Selecting Image replaces the action's success media while
preserving processing/fallback messages. Existing command defaults are preserved.

### Record and Send Video from a WhatsApp API Flow

`POST /api/videos/record-and-wait` accepts the same hostname/IP JSON body and
operator/admin Bearer authentication as `/api/videos/record`, but returns
`200 OK` only after the video has been recorded and saved. Allow a long HTTP
request timeout for capture and processing. Recording failures return `502`;
unknown cameras return `404`, ambiguous IPs return `409`, and validation/access
errors return `422`/`403`. Failed recordings are not sent to WhatsApp.

Configure an API Flow node named `data`:

- Method: `POST`
- API path: `/api/videos/record-and-wait`
- Query params: `{}`
- JSON body: `{"hostname":"{{argument}}","duration":30}`
  (use `ip` instead of `hostname` to select by IP).
- Response template: `Video {{steps.data.body.hostname}} — {{steps.data.body.duration}} seconds`
- Response type: `Video`
- Video source: `{{steps.data.body.video_id}}` or `{{steps.data.body.video_url}}`

The completed response contains `status`, `message`, `media_type: "video"`,
`video_id`, `camera_id`, `hostname`, `duration`, `file_size`, and the authenticated
`video_url`. Only commands configured with a Video response upload the selected file to GoWA's
`POST /send/video` as multipart `video`, with the response text as `caption`.
It sends the result to the invoking user's private chat, matching other API Flow
replies. It does not expose an unauthenticated static URL or send an API token to GoWA.
Text is the default response type, including for existing recording commands;
calling the recording API alone does not send video. The configured source can
select an ID or secure URL from any API node, subject to the selected token's
role, expiration, and camera group access. Deleted or missing videos are rejected.

Optional processing messages are sent before the recording starts; failure
messages handle capture/API failures. Gateway send failures are logged and
reported to the user. Webhook event deduplication also applies to these commands.
The Bot Builder Run test button records a video and previews the completed JSON;
it does not deliver a WhatsApp video. Invoke the saved command to test delivery.

### Start Video Recording by Hostname or IP

```text
POST /api/videos/record
Authorization: Bearer your-api-token
Content-Type: application/json
```

Requires an operator or admin account with access to the camera's group. The camera
must already be registered with its connection credentials and a storage group.
Provide exactly one of `hostname` or `ip`. Hostname matching is exact and
case-insensitive; IP matching is exact. Surrounding input whitespace is removed.
`duration` defaults to 10 seconds and must be between 5 and 60 seconds.

```json
{"hostname": "CCTV-GATE-01", "duration": 30}
```

Alternatively:

```json
{"ip": "192.168.1.100", "duration": 30}
```

**Responses**:
- `202 Accepted` - Recording queued in the background; includes `message`,
  `camera_id`, `hostname`, and `duration`. This does not guarantee capture success.
- `401 Unauthorized` - Missing, invalid, or expired authentication.
- `403 Forbidden` - Insufficient role or camera group access.
- `404 Not Found` - No matching registered camera.
- `409 Conflict` - Multiple matching cameras; recording is not started. Duplicate
  IPs are rejected even when only one matching camera belongs to the user's group.
- `422 Unprocessable Entity` - Missing/both selectors, blank selector, or invalid duration.

Example duplicate IP error:

```json
{"detail": "Multiple cameras match this IP address. Recording was not started. Use a unique hostname or camera ID."}
```

The existing `POST /videos/record/{camera_id}?duration=30` endpoint remains available
and applies the same role and camera group checks.

In WhatsApp Bot Builder, use an API Flow node with method `POST`, path
`/api/videos/record`, query params `{}`, and JSON body
`{"hostname":"{{argument}}","duration":30}` (or use `ip` instead of `hostname`).
Select a valid operator/admin API token. Use `{{steps.data.body.message}}` in the
response template when the node output name is `data`. The Run test button
executes the request, including starting a recording for POST nodes.

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


### Resolve camera IP for WhatsApp commands

`GET /api/cctv/resolve-ip?keyword={name}&phone_number={sender}` requires admin
session/API authentication and a whitelisted sender. Omitting `phone_number` no
longer grants unrestricted access. Assigned camera groups apply even when the
whitelist role is admin; an API user's assigned group also limits results.
Numbers beginning with `0`, `+62`, and device-qualified GoWA JIDs are normalized.
Success retains `count` and `results`; JSON errors return 403 for missing/unlisted
phones or denied camera/group access and 404 for no matching camera with an IP.
In Bot Builder API-node query parameters, set `phone_number` to `{{sender}}`.
