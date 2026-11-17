# Camera recording schedules

Recording Schedules archive camera video independently of WhatsApp. Administrators
open **Devices → Recording Schedules** or the shortcut in **Edit Camera**. An
administrator with a camera group can manage only cameras currently assigned to
that group through camera group membership.

## Setup

Run `python -m app.db.migrate` from the project environment before starting the
updated web and scheduler processes. This applies revision
`20261103_recording_schedules` and `20261103_recording_timezone`, and seeds missing configuration keys without
overwriting existing values. In Docker, use the deployment's `migrate` service
with the updated application image. Restart the web and scheduler services after
the migration. Running Alembic alone does not seed new configuration defaults.

The scheduler service runs recordings; a web-only process does not. No recordings
start until a schedule is explicitly enabled. The scheduler and web services must
share the `static/videos` volume, as in `docker-compose.yml`. FFmpeg must be
available to the scheduler. RTSP camera credentials use the existing encrypted
camera settings and ONVIF stream resolver. HTTP(S) M3U8 sources reuse the camera's
configured snapshot URL and authentication.

RTSP inputs use FFmpeg's RTSP-specific `timeout` option; HTTP/HLS inputs use
`rw_timeout`. A stream may work in manual recording yet fail in an archive if the
input options or original codec are incompatible. The supervisor reports known
connection, authentication, input-option, codec, timestamp and storage errors
without exposing raw stderr, stream URLs, passwords or tokens.

## Modes

| Mode | Settings | Behavior |
| --- | --- | --- |
| Periodic clips | Interval in minutes or five-field cron; clip duration | Records one bounded clip per due run. Interval schedules start as soon as a worker can dispatch them. Cron schedules start at their next matching time. |
| Time window | Weekdays, start/end times, segment duration | Maintains a stream during the selected window. An end before the start continues into the next day; weekdays identify the start day. |
| Continuous | Segment duration | Maintains a stream until disabled, edited, interrupted, or stopped by resource limits. |

Clips support 5–3600 seconds and intervals of 1–10080 minutes. An interval clip
cannot exceed its interval. Window/continuous segments support 30–3600 seconds.
For an all-day recording use Continuous; equal window start/end times are rejected.
Cron uses the application's APScheduler parser, including its Monday-based weekday
numbering. The UI displays the configured IANA timezone beside date columns.

Cron and windows use the database-configured timezone, independently of display
language. Pending cron runs are recalculated after a timezone change. Interval
schedules measure elapsed time between dispatches. Overnight windows and DST folds
are evaluated as UTC instants; ambiguous starts include the earlier occurrence and
ambiguous ends include the later occurrence. Nonexistent window bounds roll forward.

The Archive Recording dispatcher runs every 10 seconds by default. Its cron
override can be edited/reset in **Job Management**; changing this affects how soon
new sessions begin. Running recorders recheck settings, camera status, creator
permissions, timezone, disk reserve, and their database lease every five seconds.
Saving edits closes the active segment; enabled settings resume on a subsequent
dispatch. Disabling/deleting a schedule retains existing videos. An execution is
authorized using the administrator who last saved that schedule; removing that
user or their access stops recording.

## Files, monitoring and storage

Closed segments appear in the existing **Video Gallery**, with camera and inclusive
From/To date filters evaluated in the configured timezone, using authenticated
video/thumbnail endpoints and current camera group access. Each video has a UTC
timestamp, thumbnail where generation succeeds, SHA-256 hash, audit entry, and job
history containing its camera, schedule, and video IDs. **Job Management → Archive
Recording → History** contains segment successes and recording failures.

The recorder preserves the original video codec using stream copy and omits audio.
Segment boundaries follow camera keyframes, so actual durations can differ from
the requested duration. Playback depends on browser support for the camera codec;
archive recording does not burn the application's manual-recording watermark into
the original video. Camera-generated overlays remain in the stream.

**Configuration → Snapshot and Video Retention** also contains these controls:

| Key | Default | Purpose |
| --- | --- | --- |
| `recording_max_cameras` | `4` | Maximum simultaneous archive camera sessions per scheduler process; applies to new sessions. Valid range: 1–64. |
| `recording_min_free_mb` | `1024` | Free-space reserve in MiB on the videos filesystem. New sessions wait below the reserve; active sessions close their current segment. Valid range: 128–1048576. |
| `recording_dispatch_cron` | empty | Optional dispatcher cron; empty restores the 10-second interval. |

Deploy one scheduler process, consistent with the existing APScheduler job store.
Per-camera database leases prevent simultaneous automatic/manual recording across
web processes. Continuous/window schedules are considered before due clips; an
already active session keeps its current settings until stopped. Overlapping
schedules do not create multiple streams. Missed clip runs are coalesced/skipped;
recordings cannot reconstruct footage from an outage. Browser/API manual recording
returns a conflict if the camera already has an active recording.

Video retention remains the existing age/count policy and **soft deletes** expired
items. It does not free disk space until footage is purged. Retention holds remain
protected. Size the volume and use the existing authorized trash/purge workflow;
the recorder never automatically purges evidence to make space.

## Shutdown, interruption and recovery

The recorder keeps an FFmpeg stream connected across segments. A separate local
supervisor receives worker heartbeats over a private pipe. Worker shutdown, pipe
EOF, or missing heartbeats asks FFmpeg to finalize its current MP4; a stuck process
is terminated after a bounded wait. A lost database lease stops further recording.
Failed streams retry after a cooldown while their schedule remains eligible.
Intervals may wait until their next configured clip time.

Pending files and a credential-free recovery manifest live under
`static/videos/.recording/`; completed files live under `static/videos/archive/`.
Direct HTTP access to either location is blocked by the existing private footage
static-file rules. When a camera next starts recording, the worker retries closed
segments left by an interrupted file/database save. Deterministic video IDs avoid
duplicate records and audit entries. A successful session removes its recovery
manifest once all files have database records.

An abrupt machine failure or forced FFmpeg kill can leave the last MP4 incomplete.
Such files are retained for operator inspection, never presented as valid evidence
or silently purged. Recovering closed files still requires that camera to exist;
deleting a camera/schedule is not a way to recover pending files.

## Verification

`app/tests/test_recording_schedules.py` covers validation, Indonesian timezones,
overnight and DST windows, cron timezone reload, role/association access, CSRF,
pagination, camera leases, resource limits, metadata recovery, and isolated
migration upgrade/downgrade. Local FFmpeg tests use generated video, never a real
camera. Its SQLite fixture clones only selected tables and substitutes JSON and
integer test types without changing production PostgreSQL metadata. PostgreSQL
deployment migration and live camera compatibility must be verified on the target
deployment separately.
