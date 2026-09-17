# Scheduled snapshot recovery

Each scheduled camera runs in a fresh Python process, supervised by a bounded
thread pool. The process includes URL discovery (including ONVIF), capture,
watermarking, metadata persistence and health updates. The parent kills and reaps
a worker that exceeds `SNAPSHOT_JOB_CAMERA_TIMEOUT` (default 90 seconds, clamped
to 10–600 seconds). Queue waiting does not consume that camera's time budget.
No camera credentials are passed on the command line.

After three consecutive hard timeouts, a camera is skipped for 15 minutes before
another attempt. This circuit breaker and the 30-second attempt guard live in the
scheduler process and reset on restart. A later non-timeout result resets the
timeout streak. Skipped cameras count as unsuccessful in the batch summary.

OpenCV open/read timeouts are also supplied at construction using the FFmpeg
backend. Process isolation is the fallback for native calls that ignore those
limits. Existing HTTP snapshot and ONVIF handling remain available.

Only one scheduled snapshot job runs at once; `snapshot_concurrent_workers`
controls parallel cameras (1–32), not overlapping full jobs. Each job owns its
executor, so configuration reload cannot cancel a running batch. Failure and
timeout counts are recorded in the job metadata; mixed outcomes are `partial`.

Camera locks use OS file locks, remain held through metadata updates, and release
when the worker exits, including forced termination. The default shared directory
is `/tmp/shared/locks`; override with `SNAPSHOT_LOCK_DIR` on Windows if necessary.
All processes that coordinate camera access must use the same directory on a
filesystem supporting OS locks. Lock files intentionally remain on disk and must
not be deleted while services run. TTL no longer evicts a live owner.

## Deployment

Stop the old scheduler before starting the updated build. A rolling overlap with
the old TTL lock implementation is unsupported. Restarting is necessary to clear
the already-hung threads; updating source alone cannot unblock them. Set the new
environment variables on the scheduler service when overriding defaults.

Historical `running` execution rows are not rewritten automatically. Inspect them
as interrupted executions; new executions will record their actual outcomes.
After deployment, verify a complete batch summary and final summary, and inspect
`[TIMEOUT]` / `[CAMERA RESULT]` for camera IDs that need investigation.

Process startup adds overhead and memory use; tune parallel workers against host
capacity. A timeout can occur after a snapshot has already committed (for example
during notification delivery), so it means the overall operation was not confirmed,
not that all side effects rolled back. No automatic immediate retries or footage
deletion are performed. The existing orphaned-file maintenance remains applicable.

The independent audit-log cleanup error requires a separate retention/archive
change; this fix does not disable append-only protection.

## Verification

`python -m unittest app.tests.test_snapshot_resilience -v`

Tests cover repeated hung processes followed by successful work, lock ownership
and release after termination, capture cleanup and partial job reporting without
requiring production cameras, a database or notification delivery.

Reference: [OpenCV open-only timeout properties](https://docs.opencv.org/4.x/d4/d15/group__videoio__flags__base.html)
and [Python process timeout and cleanup](https://docs.python.org/3/library/subprocess.html).
