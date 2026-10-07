# Deployment

## Development
```bash
python -m app.db.migrate
uvicorn app.main:app --reload
```


## Production
Deploy using docker compose

Use the repository `docker-compose.yml` as the deployment source of truth.
The web, scheduler, notifier, and one-shot `migrate` service use the same image.
PostgreSQL must be healthy before `migrate` runs; application services start only
after migrations and default configuration initialization succeed.

```bash
# First installation or normal upgrade (build and start the complete stack)
docker compose up -d --build

# Check migration completion and service state
docker compose logs migrate
docker compose ps -a
```

A successful `migrate` container exits with code 0; this is expected. A non-zero
exit blocks dependent application services. Fix the migration/seed error and
run the complete stack again. Do not stamp the database to bypass a failure.

The frozen baseline creates historical base tables on an empty PostgreSQL
database, then the full Alembic chain installs schema changes, audit triggers,
and views. Existing versioned databases skip that baseline ancestor. Seeding
adds missing keys only and preserves user settings. The initial admin-account
setup remains available through the web application.

For a controlled production upgrade, back up persistent data, stop web,
scheduler and notifier, run the migration service with the new image, then
start the complete stack after a successful exit. Do not leave old workers
running against a schema being upgraded:

```bash
docker compose build
docker compose stop b-snap scheduler notifier
docker compose run --rm migrate
docker compose up -d
```

Run the last command only if migration exits successfully. A web/scheduler/
notifier restart does not apply migrations or seed defaults. Standalone web
startup also checks that schema revisions, model tables, and default keys are
ready. Running `alembic upgrade head` alone does not seed defaults; use
`python -m app.db.migrate` (or the `migrate` service) for full initialization.

For native SMB/NFS recording mounts, apply the opt-in overlay described in
[record-mounts.md](record-mounts.md); the migration service needs no mount privileges.

Set .env file like below:
```bash
DATABASE_URL=postgresql+psycopg2://bsnap_user:bsnap_pass@postgres:5432/bsnap_db
SECRET_KEY = "YourSecretKey"
TZ=Asia/Singapore
WORKERS=2
SMTP_HOST=192.168.0.1 # Your SMTP Server
SMTP_PORT=587
SMTP_USER=YourSMTPUser
SMTP_PASS=SMTPUserPassword
EMAIL_FROM=bsnap-noreply@example.com
TRUSTED_HOSTS = "*"
```

Other valid environment variables:
```bash
MAX_WEB_SESSIONS=1  # Maximum concurrent logins per user (default 1)
APP_DEBUG=1 # DEBUG mode
EMAIL_CC=admin@admin.com # Optional. This email address as CC for alert notification.
OFFLINE_ALERT_THRESHOLD_SECONDS=100 # Threshold for system sending notification, default: 1800 seconds.
```
Adjust WORKERS variable as needed: eg. 4 cpu -> 5 worker

Record check deployments must mount the SMB/NVR recording share into the scheduler container as read-only, then configure that mounted path in **Record Checks**:

```yaml
services:
  scheduler:
    volumes:
      - ./static/snapshots:/static/snapshots
      - ./static/videos:/static/videos
      - /mnt/cctv33-recordings:/mnt/cctv33-recordings:ro
```

On Windows hosts, mount the SMB share on the host first, then bind the host path into Docker. The container should receive a stable absolute path and only needs read access for scanning.

Video recording and metadata extraction require FFmpeg tooling. Put `ffmpeg` on PATH inside the container or provide the project-local `ffmpeg.exe` for Windows/local development. `ffprobe` is preferred for metadata, but B-SNAP can fall back to parsing `ffmpeg` output when `ffprobe` is unavailable.

Run:
```bash
docker compose up -d
```
