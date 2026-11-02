# Security rollout — B-Snap 2.6.1

This release addresses CA-19, CA-20, CA-27, and CA-28. Apply these changes to the
intended development/staging/deployment environment; never copy test credentials.
The source change does not edit the deployment's `.env` or contact GoWA/cameras.

## Database migration

Run the normal migration command before starting the updated web/scheduler workers:

```powershell
.\start-local.ps1 migrate
```

```bash
docker compose run --rm migrate
```

Revision `20261102_high_security` follows `20261012_email_retry_expiry`. It adds
`auth_rate_limits` and a nullable, unique `group_id` to `health_check_status`.
Existing global tracker ID 1 and historical data remain intact. Scoped trackers use
negative IDs, have a group FK, and are removed with that group. The downgrade
removes scoped progress/counters and preserves the original global tracker.

## Incoming GoWA webhook authentication

Generate a private random key using an operator-controlled terminal, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Store the same generated value in:

| Service | Environment variable |
| --- | --- |
| B-Snap web workers | `GOWA_WEBHOOK_SECRET` |
| GoWA | `WHATSAPP_WEBHOOK_SECRET`, or the applicable device-specific webhook secret |

The secret must have at least 32 characters. Do not reuse the B-Snap session key,
camera encryption key, or GoWA outbound API credentials. B-Snap never returns or
logs the signing secret. Keep TLS verification enabled when GoWA calls HTTPS.

GoWA sends `X-Hub-Signature-256: sha256=<hex HMAC-SHA256>`. B-Snap verifies the exact
raw body before JSON parsing, whitelist lookup, logging message content, or running
commands. Body size is bounded to 1 MiB. Missing/malformed/wrong/duplicate signatures
return 401; a missing/short configured secret returns 503; malformed signed JSON
returns 400; an oversized body returns 413. There is no unsigned compatibility mode.

Whitelist, role, camera-group checks, and existing provider-message-ID deduplication
remain active after signature verification. A signature authenticates bytes; it is
not itself a timestamp or nonce. Existing provider ID retention governs duplicate
suppression. Do not rely on signatures alone for indefinite replay protection.

Configure GoWA first, verify its signing configuration, configure the same key on
all B-Snap web workers, then restart/recreate the updated workers. For a rolling key
change, coordinate the services; there is no automatic accept-old-key fallback.
If a relay reserializes JSON, it must preserve the original bytes/signature or be
an explicitly trusted signer; otherwise verification correctly fails.

See the [upstream signature protocol](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/docs/webhook-payload.md#security).

## Production settings and proxy trust

Set `ENVIRONMENT=production` and generate a private `SECRET_KEY` of at least 32
characters. Known development/example keys are rejected. The key is omitted from
the security settings' representation. Invalid settings fail before accepting traffic.
Cookie Secure defaults to true in production; explicitly disabling it is rejected.
SameSite must be lax, strict, or none; none also requires Secure outside production.

For HTTPS terminated by a trusted reverse proxy:

```dotenv
ENVIRONMENT=production
BEHIND_HTTPS_PROXY=true
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
PUBLIC_ORIGIN=https://bsnap.example.com
```

Also set `TRUSTED_PROXY_HOSTS` to the actual proxy peer IPs/CIDRs seen by B-Snap.
Do not copy an illustrative network blindly. Production rejects `*`, `/0`, invalid
networks, and a missing explicit proxy list when `BEHIND_HTTPS_PROXY=true`.
Direct HTTPS deployments can leave the proxy list empty. Default development trust
is loopback; the legacy `TRUSTED_HOSTS` fallback is development/testing only and is
not a CORS domain allowlist. The reverse proxy must send Host and X-Forwarded-For/
Proto correctly and prevent clients bypassing it. Restrict exposed container ports
according to the deployment topology.

`PUBLIC_ORIGIN` is optional when request Host/scheme already match the browser.
Use it if the proxy changes the host/port. It must be an HTTP(S) origin without
credentials, a path, or query; production requires HTTPS. Use one canonical origin.

## Browser mutations / CSRF

All unsafe methods are checked before authentication DB work. Browser forms and
AJAX need matching Origin, or same-origin Referer, or browser-controlled
Sec-Fetch-Site=same-origin evidence. Cross-site and sibling-origin same-site browser
requests are rejected. Ambiguous/invalid origins and origin-less unsafe cookie
requests fail closed with 403 and `CSRF_REJECTED`.

Native browser POST forms and same-origin fetch already supply this evidence;
no form-token migration or JavaScript token handling is required. An HTTP client
using a browser cookie must provide correct origin evidence. For integrations use
Bearer API authentication without browser session/Remember Me cookies. Supplying a
Bearer header alongside browser cookies does not bypass the check.

The only unsafe public exemption is POST `/webhook/gowa`, protected by its own
HMAC. Auth, setup, MFA and logout are included in browser-origin checks. GET
`/logout` only renders a confirmation; actual logout requires POST. Signed media
GET requests retain their existing access rules. Requests are never automatically
replayed after a CSRF/authentication error.

## Shared login/OTP attempt limits

Password and OTP stages each allow 30 attempts/IP/minute and 10 attempts/identity
per five minutes. OTP login and MFA enrollment share the OTP identity window.
Attempts are counted before password/OTP checks, including successful attempts.
These constants live in `app/utils/auth_rate_limit.py`, not duplicate config defaults.
Atomic PostgreSQL upserts share counters across workers. 429 responses include
Retry-After; counter/database failures return generic 503 without bypassing auth.
Expired opaque digest buckets are cleaned during authentication; no passwords,
OTP codes, or clear IP/user identifiers are stored in this table. Ensure trusted
proxy settings are correct so forwarded IPs cannot bypass IP windows.

## Health group boundaries

Status, uptime, history, counts, group choices, SLA, CSV/Excel/PDF report selection,
and single/bulk triggers apply the current user's group. Cameras use association
membership; a stale primary-group scalar cannot grant access. NVRs use their
existing group relation. Group NULL preserves global access, including operators.

Bulk progress is isolated per group, independent from scheduler/global progress.
Repeated triggers for a running scope return 409; PostgreSQL row locks prevent
concurrent starts for that scope. Worker queries recheck membership before network
work. Restricted bulk checks do not run global email-log cleanup. Existing recovery
resets interrupted scoped trackers along with global progress after a restart.

Existing scheduled-report definitions have no persisted group scope; management
of these global definitions now requires group NULL. Restricted users can still
download their authorized on-demand reports. No report scheduler is introduced.

## Verification and operational limits

Regression tests use mocked camera/GoWA/SMTP transport, isolated SQLite subsets,
and PostgreSQL 17 disposable schemas for migration and concurrent limits/progress.
No production schema/configuration is modified by the test run. Test real HTTPS
proxy headers, native browser forms, GoWA signatures/retries, operator group
changes, and worker restart during deployment acceptance. Docker/image and live
external integrations still require the operator's deployment environment.
