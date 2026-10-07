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

## Deployment credentials and access

Copy `.env.example` to `.env` for a new deployment and set the existing
application keys plus `POSTGRES_PASSWORD`. Compose rejects an empty/missing
PostgreSQL password instead of using a built-in credential. Set
`POSTGRES_DB`/`POSTGRES_USER` if the existing database uses other names; the
healthcheck reads those settings from the PostgreSQL container environment.

`DATABASE_URL` must match the database name, user and password. Within the
Compose network use hostname `postgres` and port `5432`; local Python processes
use `localhost` and `POSTGRES_PORT`. URL-encode special characters in the URL
password, but keep the original password in `POSTGRES_PASSWORD`. Keep the same
`SECRET_KEY` and `ENCRYPTION_KEY` across application services and upgrades.

For an existing `postgres_data` volume, set credentials matching the active
database. Changing `POSTGRES_PASSWORD` in `.env` does not alter the existing
PostgreSQL role password; PostgreSQL initialization variables only apply to an
empty data directory. Rotate a previously exposed password explicitly through
database administration, then update both `.env` values. Never delete the volume
to change a password.

Validate configuration without printing resolved credentials:

```bash
docker compose config --quiet
```

PostgreSQL's published port is restricted to `127.0.0.1` (default `5432`,
configurable with `POSTGRES_PORT`). Application containers still connect directly
to `postgres:5432`. Use a secure tunnel or explicit reviewed overlay for remote
administration rather than exposing this port on all interfaces.

pgAdmin is disabled in the default stack. Set `PGADMIN_DEFAULT_EMAIL` and
`PGADMIN_DEFAULT_PASSWORD` to enable it; blank credentials cause pgAdmin itself
to reject startup, without preventing the default application stack from loading.
Its published port is also restricted to localhost:

```bash
docker compose --profile admin up -d pgadmin
```

Open `http://127.0.0.1:5050` on the Docker host (or use a secure tunnel).
`PGADMIN_PORT` can change the host port. Environment values are deployment
configuration, not Docker secrets: users with Docker administration access can
inspect container environment variables. Protect `.env` on the deployment host.

The base web/scheduler services retain `NET_RAW` for ICMP health checks and no
longer use privileged mode. All four application services enable
`no-new-privileges`; migrate/notifier also drop all Linux capabilities. Native
mounts remain opt-in through the restricted Linux/AppArmor overlay and still
require root plus `SYS_ADMIN` for web/scheduler. Default Docker capabilities and
the root runtime of web/scheduler have not yet been replaced by a non-root setup.

For local image builds, use the development overlay with the base file:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The development overlay only changes image names, preserving base startup,
volumes and security. `docker-compose-build.yml` is a legacy standalone import
configuration; its pgloader target URL must be supplied through
`PGLOADER_TARGET_URL` when explicitly enabling its `migrate` profile. This is
separate from the normal Alembic migration service.

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
      - ./static/snapshots:/app/static/snapshots
      - ./static/videos:/app/static/videos
      - /mnt/cctv33-recordings:/mnt/cctv33-recordings:ro
```

On Windows hosts, mount the SMB share on the host first, then bind the host path into Docker. The container should receive a stable absolute path and only needs read access for scanning.

Video recording and metadata extraction require FFmpeg tooling. Put `ffmpeg` on PATH inside the container or provide the project-local `ffmpeg.exe` for Windows/local development. `ffprobe` is preferred for metadata, but B-SNAP can fall back to parsing `ffmpeg` output when `ffprobe` is unavailable.

Run:
```bash
docker compose up -d
```

## Persistent storage

Web and scheduler share the same host directories at the same container paths:

| Host directory / volume | Container path | Services |
| --- | --- | --- |
| `./static/snapshots` | `/app/static/snapshots` | web, scheduler |
| `./static/videos` | `/app/static/videos` | web, scheduler |
| `./logs` | `/app/logs` | web, scheduler |
| `./archives/audit_logs` | `/app/archives/audit_logs` | web, scheduler |
| `shared_tmp` | `/tmp/shared` | web, scheduler |
| `postgres_data` | `/var/lib/postgresql/data` | PostgreSQL |
| `pgadmin_data` | `/var/lib/pgadmin` | pgAdmin (optional) |

Keep footage writable in both web and scheduler: manual capture, soft-delete,
restore and cleanup still need the existing access. Camera file locks and reload
flags use `shared_tmp`; it is coordination storage for a single Docker host,
not a backup or a mechanism for coordinating multiple hosts. Managed SMB/NFS
mount roots remain private to each service's mount namespace.

The scheduler checks capacity of the snapshot filesystem by default, instead of
the container's `/` filesystem. Set `STORAGE_MONITOR_PATH` to an existing absolute
path to monitor another device. This checks one filesystem; when snapshots,
videos and archives live on separate devices, monitor each additional device
with deployment monitoring. Directory-size breakdown uses application-root
paths and the existing `LOG_DIR` setting, regardless of working directory.

Audit archive files are encrypted with `AUDIT_ARCHIVE_KEY`, a separate Fernet key
from the camera `ENCRYPTION_KEY`. Generate it once, configure the same value for
web and scheduler, and store a secure backup. Missing/invalid keys make archiving
fail before file publication or deletion of source audit rows; no temporary key
is generated or printed. Existing archives still require their original key.
Archive metadata stores a SHA-256 key fingerprint for new archives.

For deployments upgraded from the previous configuration, recover any archives
written inside an old scheduler container before removing that container. The
new shared bind mount hides files previously written in its writable layer. If
pgAdmin is already in use, export its existing configuration before recreating
it with the new volume; pre-existing container state is not copied automatically.

Back up PostgreSQL consistently, snapshots/videos, audit archive files, and keys
stored securely outside the image. `docker compose down` retains named volumes;
`docker compose down -v` deletes them. Runtime archives, backups, logs and test
outputs are excluded from the Docker build context. Files are not moved or
removed by these configuration changes.
