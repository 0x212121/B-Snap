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
docker compose build b-snap
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

## Shared image build

Only `b-snap` defines a build in the base Compose file. Migrator, scheduler and
notifier reuse the same image reference through a YAML anchor. Build before
running an individual worker/migrator on a fresh host:

```bash
docker compose build b-snap
docker compose up -d --no-build
```

The Dockerfile uses BuildKit (Dockerfile syntax v1) with three stages:

- `python-wheels`: compile/download wheels using the existing Python runtime
  version; build tools and headers stay in this stage.
- `frontend`: install from `package-lock.json` using `npm ci`, then build minified
  Tailwind CSS from templates, Python, JavaScript and CSS source files.
- `runtime`: install wheels offline through a temporary BuildKit mount, copy
  application code and generated CSS, and retain FFmpeg, curl, CIFS/NFS helpers
  plus native runtime libraries. Node/npm, compilers and the wheelhouse are not
  copied from their build stages into the final image.

Pip/npm download caches are reused during builds. Runtime startup validates CSS
instead of attempting to run npm. The build checks Python dependency consistency,
imports native image/database/crypto libraries, checks ffprobe, and validates the
OCI version label against both `pyproject.toml` and `app/version.py`.

The current default image tag and `APP_VERSION` build argument follow the package
version. On a version bump, update both defaults; inconsistent source/label
versions fail the build. For a manual build supply `--build-arg APP_VERSION=...`
matching the checked-out package version. `PYTHON_IMAGE` can override the shared
base for both wheel-builder and runtime; keep their ABI and Debian libraries
compatible. No image-size reduction is claimed until a Docker build is measured.

For CI-built images, set `BSNAP_IMAGE` to the published release tag or digest,
pull it once, then deploy without local builds or implicit application-image pulls.
On a fresh host, also pull PostgreSQL (and pgAdmin if enabling the admin profile)
before using `--pull never`:

```bash
docker compose pull b-snap postgres
docker compose up -d --no-build --pull never
```

For an upgrade, stop old application services and run the migration service
successfully with that image before starting web/scheduler/notifier, as described
above. `BSNAP_IMAGE` changes the image reference, not the application version.
The local-image development overlay still uses `b-snap:local` for all four roles.


## Container readiness, budgets and shutdown

The web container probes `GET /readyz` rather than `/version`. This exact public
GET endpoint returns only `ready` (HTTP 200) or `unavailable` (HTTP 503), bypasses
login/setup and API-row logging, and checks completed application startup plus
`SELECT 1` on the current database connection pool. It works before the first
admin account is created. It does not report individual camera availability.

Scheduler and notifier publish atomic process-local heartbeats under
`/tmp/bsnap-health`; they are not stored on the shared reload-flag volume. Docker
runs `python -m app.core.service_health scheduler` or `notifier` every 30 seconds.
Scheduler health requires a successful configuration reload, live PostgreSQL
query, and a running scheduler thread; its heartbeat expires after 180 seconds.
Notifier checks its registered LISTEN connection every 30 seconds, reconnects
following failures, and expires after 90 seconds without a successful query.
A connection termination invalidates notifier health immediately. A worker
heartbeat checks the worker control loop; it does not prove that every job or
notification completed successfully. Notifications are queued and wake-up signals
published in one transaction; health queries and deliveries serialize access to
the listener connection. Notification payloads and raw connection errors are not
printed to container logs.

`docker compose ps` and `docker compose logs --tail 100 scheduler notifier b-snap`
show status and recent diagnostics. Compose does not restart a running container
just because its healthcheck reports unhealthy: `unless-stopped` restarts exited
processes. Alert on persistent unhealthy states through your monitoring system;
inspect the cause before restarting a service. A configuration reload failure
stops the scheduler process and clears its health while draining active jobs.
Keep exactly one scheduler and one notifier for this job store/channel; these
workers are not designed for replicas or concurrent rolling replacements.

The Compose defaults are starting limits to tune to host capacity and camera
workload; they are ceilings, not reserved RAM or CPU:

| Service | CPU ceiling | Memory ceiling | Stop grace | Main DB pool / overflow |
| --- | --- | --- | --- | --- |
| Web | 2 | 2 GiB | 120 s | 5 / 5 per Gunicorn worker |
| Scheduler | 2 | 4 GiB | 180 s | 10 / 5 |
| Notifier | 0.5 | 512 MiB | 60 s | 1 / 0 for startup; one asyncpg listener |
| Migrator | 1 | 512 MiB | 60 s | 2 / 0 |
| PostgreSQL | 2 | 2 GiB | 60 s | Server connection limit |
| pgAdmin (optional) | 1 | 512 MiB | 60 s | Independent admin connections |

Use the role variables in `.env.example` (`WEB_CPUS`, `SCHEDULER_MEM_LIMIT`, etc.)
to adjust limits. A memory ceiling can terminate a process if its workload exceeds
that budget; measure capture/video workloads before choosing tighter values.
`WEB_CONCURRENCY` takes precedence over `WORKERS`; both entrypoint reporting and
Gunicorn use that order. Compose fixes the internal web bind to port 8080 to match
the published port and probe. For a different host port, override the published
port in Compose while retaining the internal port.

`WEB_DB_POOL_SIZE`, `WEB_DB_MAX_OVERFLOW`, `SCHEDULER_DB_POOL_SIZE`,
`SCHEDULER_DB_MAX_OVERFLOW` and `MIGRATE_DB_POOL_SIZE` feed role-specific engine
settings through Compose. Outside Docker, `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`
control the engine directly (unset defaults remain 20 and 10). All deployments
now use pool pre-ping and a five-second PostgreSQL connect timeout; pool timeout
and recycle use `DB_POOL_TIMEOUT` and `DB_POOL_RECYCLE` (native unset defaults 30
and 1800 seconds, Compose defaults 5 and 1800). Keep sizes positive, overflow
non-negative, and timeouts positive. Invalid values fail startup instead of
creating unbounded pools. `DB_POOL_RECYCLE=-1` disables recycling if needed.

With two web workers, the steady main SQLAlchemy pools allow at most
`2 * (5 + 5) + (10 + 5) = 35` connections. Allow additional connections for the
notifier, per-web-worker asyncpg polling, startup hooks, migration, administration
and backups. These pool ceilings are not the total PostgreSQL connection budget.
Increasing web worker count multiplies its pool budget; increasing capture
concurrency also raises scheduler demand. Leave headroom under PostgreSQL's
`max_connections` rather than setting each container pool to the server limit.

Application containers use `init: true` for child-process reaping and signal
forwarding. On SIGTERM/SIGINT, scheduler stops its control loop promptly, pauses
new jobs and waits for already-running jobs. Notifier stops listening, allows up
to 30 seconds for pending deliveries, and closes the PostgreSQL connection with
a five-second close timeout. Gunicorn uses `GRACEFUL_TIMEOUT` (default 90 s),
which must stay below `WEB_STOP_GRACE_PERIOD` (default 120 s). Scheduler job
batches may take longer than the default 180-second grace; increase its grace to
cover the longest normal job. Docker sends SIGKILL after the configured grace,
so a timeout can still interrupt captures or pending deliveries. This shutdown
handling does not add durable replay for PostgreSQL notifications lost while the
listener is offline.

All six services use Docker's `local` log driver with rotation at 10 MiB and five
files per container. This bounds Docker stdout/stderr logs; Python file handlers
continue their existing rotation under `./logs`, and audit-archive retention is
managed separately. Container log settings take effect after container recreation.

Before production rollout, build and smoke-test the image on a Docker host:

```bash
docker compose config --quiet
docker compose build b-snap
docker compose up -d --no-build
docker compose ps
curl --fail http://127.0.0.1:8080/readyz
docker compose exec scheduler python -m app.core.service_health scheduler
docker compose exec notifier python -m app.core.service_health notifier
```

In an isolated deployment, verify a database outage makes readiness/workers
unhealthy, restores notifier health after reconnect, and inspect logs when using
`docker compose stop scheduler notifier b-snap` to confirm orderly shutdown.
Do not run outage or capture-interruption checks against production camera jobs.
