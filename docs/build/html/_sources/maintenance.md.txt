# Maintenance & Troubleshooting

## Database

- Backup with `pg_dump`.
- Restore with `psql`.

## Logs

- B-SNAP logs in `logs`.
- GoWA logs are available from the GoWA service/container.
- Scheduler logs show background job registration and execution errors.

## Common Issues

- **DB migration error**: check Alembic `versions/` and run `alembic upgrade head`.

## Current Operational Checks

- **WhatsApp not receiving messages**: check GoWA base URL, auth secret, login status, and default receiver. Use `user:password` for `APP_BASIC_AUTH`; use token-only values only for older `AUTH_TOKEN` deployments.
- **Group receiver not working**: load groups from GoWA and use the group JID format, for example `120363xxxxxxxx@g.us`.
- **Record check cleanup not visible in Job Management**: restart the scheduler service so `cleanup_record_checks` is registered in APScheduler.
- **Record check history grows too large**: verify `retention_record_check_days`. Default is 90 days. Cleanup deletes run/check/event history only; sources, mappings, and current statuses are preserved.
- **Video playback after recording returns `VIDEO_ACCESS_BLOCKED`**: use `/api/videos/secure/{video_id}`. Direct `/static/videos/*` access is intentionally blocked.
- **Video thumbnail missing**: ensure `ffmpeg` is on PATH or `ffmpeg.exe` exists in the project root.
- **Video duration/resolution shows `N/A`**: install `ffprobe` or ensure `ffmpeg` is available so B-SNAP can use the fallback metadata parser.
