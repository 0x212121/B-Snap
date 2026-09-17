# Email Notifications

B-Snap provides intelligent email notification system for CCTV monitoring with multi-layer anti-flooding protection using circuit breaker pattern and immediate cooldown mechanisms.

## Overview

The notification system sends alerts for:
- **Offline incidents** - Camera goes offline
- **Tamper alerts** - Camera tampering detected (blur, etc.)
- **Recovery alerts** - Camera comes back online
- **Record-check alerts** - Mounted SMB/NVR record folders become stale or missing
- **WhatsApp reports** - Daily camera reports and storage alerts through GoWA

## WhatsApp Gateway (GoWA)

B-SNAP can send WhatsApp notifications directly through GoWA.

### Authentication

- Use `APP_BASIC_AUTH=user:password` on current GoWA deployments, then enter `user:password` in the B-SNAP GoWA auth field.
- Older GoWA deployments using `AUTH_TOKEN` can still use a token-only value.
- Leave the auth field empty only when GoWA is intentionally unauthenticated and isolated.

### Receivers

Default receivers can be personal numbers or WhatsApp group JIDs:

```text
6281234567890
120363xxxxxxxx@g.us
```

Multiple receivers are comma-separated. The Config UI can query GoWA groups from `GET /user/my/groups` and append the selected group JID automatically.

### Record-Check Notifications

Record-check alerts are sent when:

- a folder enters `stale`
- a previously known folder becomes `missing`
- an alerted folder recovers to `healthy`

Recovery notifications include any remaining stale/missing folders so operators can see what still needs attention.

## Incident Time Accuracy (Bug Fix)

### The Problem

Previously, tamper alert emails showed incorrect incident times:

**Before (Bug):**
- Snapshot taken at: **09:52:08** (detected blur)
- Email queued/retry delays: 09:52:10, 09:52:15, 09:52:20
- Email finally sent at: **09:52:25**
- **Email showed: "Waktu Kejadian: 09:52:25 UTC"** ← Wrong!

This caused:
- Misleading timestamps in email notifications
- Incorrect incident records in database
- Multiple emails showing different times for same incident

### The Solution

**After (Fixed):**
- Snapshot taken at: **09:52:08** (detected blur)
- Email sent at: **09:52:25**
- **Email shows: "Waktu Kejadian: 09:52:08 UTC"** ← Correct!

### Implementation

```python
# OLD (Bug): Using current time
def send_tamper_alert(db, camera, reason, path):
    incident_time = datetime.now(timezone.utc)  # ← Wrong: email send time
    ...

# NEW (Fixed): Using actual snapshot timestamp
def send_tamper_alert(db, camera, reason, path, incident_time=None):
    if incident_time is None:
        incident_time = datetime.now(timezone.utc)  # Fallback
    ...
    # Email body uses incident_time instead of current time
    plain_body = f"Waktu Kejadian: {incident_time.strftime('%d/%m/%Y %H:%M:%S')} UTC"
```

### Key Changes

1. **New Parameter**: `incident_time` added to `send_tamper_alert()`
2. **Snapshot Timestamp**: `snapshot.timestamp` passed as incident_time
3. **Database Record**: `incident_started_at` stores actual detection time
4. **Cooldown Calculation**: Based on incident_time, not email send time

### Email Example

**Before (Bug):**
```
CCTV Handak_AV_PTZ2 mengalami anomali/tampering dengan indikasi: blur
Waktu Kejadian: 30/03/2026 09:52:25 UTC  ← Email send time (wrong)
```

**After (Fixed):**
```
CCTV Handak_AV_PTZ2 mengalami anomali/tampering dengan indikasi: blur
Waktu Kejadian: 30/03/2026 09:52:08 UTC  ← Actual detection time (correct)
```

## Circuit Breaker Pattern

To prevent email flooding when cameras have persistent issues, B-Snap implements a **circuit breaker pattern** with the following behavior:

### How It Works

```
┌─────────────────┐
│  Camera Issue   │
│   Detected      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  Check Circuit  │────▶│  Suppressed?    │────▶ Skip
│    Breaker      │     │   (3 failures)  │
└─────────────────┘     └─────────────────┘
         │ No
         ▼
┌─────────────────┐     ┌─────────────────┐
│  Attempt Send   │────▶│    Success?     │
│                 │     │                 │
└─────────────────┘     └────────┬────────┘
         │                       │
         │ No                    │ Yes
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│ Increment Fail  │     │  Reset Counter  │
│     Count       │     │   Success=0     │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│ Count >= 3 ?    │────▶ Suppress for 60 min
└─────────────────┘
```

### Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_NOTIFICATION_FAILURES` | 3 | Consecutive failures before suppression |
| `NOTIFICATION_SUPPRESS_MINUTES` | 60 | Suppression duration after max failures |
| `TAMPER_DEDUPLICATION_MINUTES` | 15 | Minimum time between tamper alerts |

### Database Fields

Three fields are added to the `cameras` table:

```sql
notification_fail_count INTEGER DEFAULT 0
notification_suppressed_until TIMESTAMP WITH TIME ZONE
last_notification_at TIMESTAMP WITH TIME ZONE
```

### Suppression Scenarios

1. **SMTP Not Configured**
   - System checks SMTP configuration before attempting to send
   - Logs warning once: "SMTP not configured. Skipping..."
   - No log entries created in database (prevents log flooding)

2. **Consecutive Failures**
   - Each failure increments `notification_fail_count`
   - After 3 failures: notifications suppressed for 60 minutes
   - Example timeline:
     - 09:00 - Send failed (count=1)
     - 09:05 - Send failed (count=2)
     - 09:10 - Send failed (count=3, suppression activated until 10:10)
     - 09:15 - Camera offline detected - Notification suppressed
     - 09:20 - Tamper detected - Notification suppressed
     - 10:15 - Camera offline detected - Retry allowed (suppression expired)

3. **Recovery Reset**
   - When camera comes back online, suppression is automatically reset
   - Counter reset to 0
   - New incidents will trigger notifications normally

### Deduplication

For tamper alerts, the system prevents duplicate notifications:

- Same camera + same reason within 15 minutes = 1 notification only
- Different reasons (blur vs offline) = separate notifications
- Different cameras = separate notifications

Example:
```
09:00 - Tamper (blur) detected on Camera A → Send email
09:05 - Tamper (blur) detected on Camera A → Skip (duplicate)
09:10 - Offline detected on Camera A → Send email (different reason)
09:20 - Tamper (blur) detected on Camera A → Send email (>15 min passed)
```

## Tamper Alert Cooldown (Immediate Protection)

In addition to circuit breaker, tamper alerts have **immediate cooldown protection** with multiple layers to prevent per-second flooding.

### How It Works

```
Scheduler initiates snapshot
       │
       ▼
┌─────────────────┐     ┌─────────────────┐
│ Scheduler Rate  │────▶│  < 30 sec since │────▶ Skip
│    Limiting     │     │   last snapshot │
│  (30 seconds)   │     │                 │
└─────────────────┘     └─────────────────┘
         │ No
         ▼
Take snapshot → Detect tamper
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│ Check CameraHealth│────▶│  In Cooldown?   │────▶ Skip
│   cooldown      │     │  (15 minutes)   │
└─────────────────┘     └─────────────────┘
         │ No
         ▼
Set Cooldown (15 min) BEFORE send
         │
         ▼
    Attempt Send
         │
    ┌────┴────┐
    │         │
  Success   Failure
    │         │
    ▼         ▼
   Done    Cooldown already set
           (no retry until expired)
```

### Key Differences

| Mechanism | Trigger | Duration | Purpose |
|-----------|---------|----------|---------|
| **Scheduler Rate Limit** | Every snapshot | 30 seconds | Prevent concurrent snapshots |
| **Alert Cooldown** | Tamper detected | 15 minutes | Prevent alert flooding |
| **Circuit Breaker** | 3 send failures | 60 minutes | Handle persistent failures |

### Database Fields

Added to `camera_health` table:
- `alert_cooldown_until` - When next alert can be sent (15-minute cooldown)
- `last_alert_reason` - For deduplication (same reason = skip)

### Four-Layer Protection

1. **Scheduler Rate Limiting** (First Defense)
   ```python
   # In scheduler.py - prevents multiple snapshots
   if elapsed < MIN_SNAPSHOT_INTERVAL_SECONDS:  # 30 seconds
       return {"status": "skipped", "reason": "rate_limited"}
   ```

2. **CameraHealth Cooldown Check** (Second Defense)
   ```python
   if health.alert_cooldown_until > now:
       return False  # Skip immediately
   ```

3. **CameraHealth Deduplication** (Third Defense)
   ```python
   if (health.last_alert_reason == reason and 
       health.last_email_sent > 15_minutes_ago):
       return False
   ```

4. **Database Log Check** (Final Fallback)
   ```python
   recent_log = db.query(...).filter(
       sent_at > 15_minutes_ago
   ).first()
   ```

### Immediate Cooldown Pattern

The key innovation is setting cooldown **BEFORE** attempting to send:

```python
def send_tamper_alert(db, camera, reason, path):
    # 1. SET COOLDOWN FIRST
    health.alert_cooldown_until = now + 15_minutes
    db.commit()
    
    # 2. Then attempt send
    try:
        send_email(...)
    except Exception:
        # Cooldown already set, won't retry immediately
        pass
```

This ensures that even if the function is called 100 times per second, only the first call will attempt to send email. Subsequent calls will see the cooldown and exit immediately.

### Cooldown Behavior

| Scenario | Cooldown | Notes |
|----------|----------|-------|
| Tamper detected | 15 minutes | Set BEFORE attempting send |
| Tamper send fails | 15 minutes | Cooldown already set, no retry until expired |
| Recovery detected | 5 minutes | Shorter cooldown for good news |
| Recovery send fails | 5 minutes | Short cooldown, can retry sooner |

**Important:** Cooldown is set **before** attempting to send email. This ensures that even if the send fails (SMTP error), the system won't immediately try again.

## Alert Types

### Offline Incident

Triggered when camera goes offline.

**Email Subject:**
```
🚨 [Group Name] CCTV Alert – Camera Name – Offline
```

**Circuit Breaker Behavior:**
- One email per incident (tracked by `incident_started_at`)
- If send fails, retry queued with exponential backoff
- After 3 failures, suppressed until camera recovers

### Tamper Alert

Triggered when camera tampering is detected (blur, lens obstruction, etc.).

**Email Subject:**
```
⚠️ [Group Name] CCTV Alert – Camera Name BLUR
```

**Cooldown Behavior:**
- Immediate 15-minute cooldown on first detection
- Same camera + same reason = 1 alert per 15 minutes
- Cooldown set before attempting send (prevents flooding on failure)
- Continuous tampering (blur every second) = 1 alert, then suppressed

**Circuit Breaker Behavior:**
- Deduplication: 15-minute window for same camera + reason
- After 3 failures, suppressed for 60 minutes

### Recovery Alert

Triggered when camera comes back online.

**Email Subject:**
```
✅ [Group Name] CCTV Recovery – Camera Name
```

**Circuit Breaker Behavior:**
- Always attempts to send (recovery is good news)
- Resets failure counter and suppression for the camera
- Less critical, so failures don't increment counter

## API Functions

### `is_notification_suppressed(db, camera)`

Check if notifications are currently suppressed for a camera.

```python
from app.utils.email_notifier import is_notification_suppressed

if is_notification_suppressed(db, camera):
    logger.info("Notification suppressed")
    return False
```

### `record_notification_failure(db, camera, error_message)`

Record a failed notification attempt.

```python
from app.utils.email_notifier import record_notification_failure

try:
    send_email(...)
except Exception as e:
    record_notification_failure(db, camera, str(e))
```

### `record_notification_success(db, camera)`

Record a successful notification and reset failure counter.

```python
from app.utils.email_notifier import record_notification_success

send_email(...)
record_notification_success(db, camera)
```

### `is_alert_in_cooldown(health, alert_reason)`

Check if alert is in cooldown period (snapshot_utils level).

```python
from app.utils.snapshot_utils import is_alert_in_cooldown

if is_alert_in_cooldown(health, "blur"):
    logger.info("Alert in cooldown, skipping")
    return False
```

### `set_alert_cooldown(health, reason)`

Set cooldown period after sending alert.

```python
from app.utils.snapshot_utils import set_alert_cooldown

send_tamper_alert(db, camera, reason, path)
set_alert_cooldown(health, reason)  # Sets 15-minute cooldown
```

### `reset_notification_suppression(db, camera_id)`

Manually reset suppression for a camera (called on recovery).

```python
from app.utils.email_notifier import reset_notification_suppression

# When camera comes back online
reset_notification_suppression(db, camera.id)
```

## Monitoring

### Logs

The notification system logs important events:

```python
# Suppression activated
logger.warning(
    "Camera %s has reached %d consecutive notification failures. "
    "Notifications suppressed for %d minutes.",
    camera.hostname, 3, 60
)

# Skipping due to suppression
logger.info(
    "Offline notification suppressed for camera %s due to previous failures",
    camera.hostname
)

# Deduplication
logger.info(
    "Skipping tamper alert for camera %s - recent alert exists",
    camera.hostname
)
```

### Database Queries

Check suppression status:

```sql
-- List cameras with active suppression (circuit breaker)
SELECT hostname, notification_fail_count, notification_suppressed_until
FROM cameras
WHERE notification_suppressed_until > NOW();

-- List cameras in cooldown (tamper alerts)
SELECT c.hostname, h.alert_cooldown_until, h.last_alert_reason
FROM camera_health h
JOIN cameras c ON h.camera_id = c.id
WHERE h.alert_cooldown_until > NOW();

-- List cameras with high failure counts
SELECT hostname, notification_fail_count
FROM cameras
WHERE notification_fail_count > 0
ORDER BY notification_fail_count DESC;

-- Verify incident time accuracy (should match snapshot timestamp)
SELECT 
    l.camera_name,
    l.reason,
    l.incident_started_at,
    s.timestamp as snapshot_timestamp,
    EXTRACT(EPOCH FROM (l.incident_started_at - s.timestamp)) as diff_seconds
FROM camera_email_notification_logs l
JOIN snapshots s ON l.camera_id = s.camera_id 
    AND s.timestamp BETWEEN l.incident_started_at - INTERVAL '1 minute' 
                        AND l.incident_started_at + INTERVAL '1 minute'
WHERE l.reason LIKE 'tamper%'
ORDER BY l.incident_started_at DESC
LIMIT 10;
```

## Troubleshooting

### Incorrect Incident Time in Emails

If emails show wrong incident times (email send time instead of detection time):

**Check Function Signature:**
```python
from app.utils.email_notifier import send_tamper_alert
import inspect
print(inspect.signature(send_tamper_alert))
# Should show: (db, camera, reason, snapshot_path, incident_time=None)
```

**Verify Snapshot Timestamp:**
```sql
-- Check if snapshot timestamp matches email incident time
SELECT 
    cam.hostname,
    snap.timestamp as actual_detection_time,
    log.incident_started_at as email_incident_time,
    CASE 
        WHEN snap.timestamp = log.incident_started_at THEN 'CORRECT'
        ELSE 'MISMATCH'
    END as status
FROM snapshots snap
JOIN camera_email_notification_logs log ON snap.camera_id = log.camera_id
JOIN cameras cam ON snap.camera_id = cam.id
WHERE snap.is_tampered = true
  AND log.reason LIKE 'tamper%'
ORDER BY snap.timestamp DESC
LIMIT 5;
```

**Fix:** Update to latest version which includes `incident_time` parameter.

### No Emails Being Sent

1. **Check SMTP Configuration**
   ```python
   from app.utils.smtp_config import get_smtp_config
   config = get_smtp_config()
   print(config["is_configured"])  # Should be True
   ```

2. **Check Suppression Status**
   ```sql
   SELECT hostname, notification_suppressed_until
   FROM cameras
   WHERE notification_suppressed_until > NOW();
   ```

3. **Check Failure Counts**
   ```sql
   SELECT hostname, notification_fail_count, last_notification_at
   FROM cameras
   WHERE notification_fail_count > 0;
   ```

### Reset Suppression Manually

If you need to manually reset suppression for a camera:

```python
from app.db.database import SessionLocal
from app.utils.email_notifier import reset_notification_suppression

db = SessionLocal()
reset_notification_suppression(db, "camera-uuid-here")
db.close()
```

### Too Many Failed Notifications

If you see many failed notifications:

1. Check SMTP credentials in `/config` page
2. Check network connectivity to SMTP server
3. Check SMTP server logs
4. Verify recipient email addresses are valid

### Email Flooding (Per-Second Alerts)

If you're seeing alerts every second for the same camera (e.g., 375,917 emails in short time):

**Immediate Stop:**
```python
from app.db.database import SessionLocal
from app.models.health import CameraHealth

# Stop all tamper alerts immediately
db = SessionLocal()
db.query(CameraHealth).update({
    'alert_cooldown_until': db.func.now() + db.text("INTERVAL '1 hour'"),
    'tamper_status': 'normal',
    'consecutive_tamper': 0
})
db.commit()
db.close()
```

**Check Scheduler Interval:**
```sql
-- Check if snapshot interval is too short
SELECT key, value FROM configuration WHERE key = 'snapshot_interval_minutes';
-- Should be at least 60 (1 hour), default is 480 (8 hours)
```

**Check for Multiple Scheduler Instances:**
```bash
# Check if multiple scheduler processes are running
ps aux | grep scheduler
# Should only show ONE python process with scheduler
```

**Check Cooldown Status:**
```sql
-- Check which cameras are in cooldown
SELECT c.hostname, h.alert_cooldown_until, h.last_alert_reason,
       h.tamper_status, h.consecutive_tamper
FROM camera_health h
JOIN cameras c ON h.camera_id = c.id
WHERE h.alert_cooldown_until > NOW()
ORDER BY h.alert_cooldown_until DESC;
```

**Check Recent Alert Logs:**
```sql
-- Check recent tamper alerts (should be spaced 15+ minutes apart)
SELECT camera_name, reason, sent_at, success, error_message
FROM camera_email_notification_logs
WHERE reason LIKE 'tamper%'
  AND sent_at > NOW() - INTERVAL '1 hour'
ORDER BY sent_at DESC
LIMIT 20;
```

**Root Causes:**

1. **Scheduler interval too short** (< 15 minutes)
   - Fix: Set `snapshot_interval_minutes` to at least 60 in /config

2. **Multiple scheduler instances running**
   - Fix: Kill all but one scheduler process
   - Check systemd/supervisor config to prevent duplicates

3. **Cooldown not being set**
   - Check logs for "[COOLDOWN SET]" messages
   - If missing, check database permissions for camera_health table

4. **SMTP not configured + no cooldown**
   - Before the fix: SMTP failure didn't set cooldown
   - After fix: Cooldown set before attempting send

**Prevention:**

The fix implements four layers:
1. **Scheduler rate limiting**: 30-second minimum between snapshots per camera
2. **Immediate cooldown**: Set in DB before attempting send
3. **Deduplication**: Same reason within 15 minutes = skip
4. **Circuit breaker**: 3 failures = 60-minute suppression

**Verification:**

After fix, you should see in logs:
```
[COOLDOWN SET] blur cooldown set for CameraX until 2026-03-30T10:52:08
[COOLDOWN ACTIVE] Alert for CameraX suppressed for 842 more seconds
```

Instead of:
```
send_tamper_alert triggered for CameraX (blur)
send_tamper_alert triggered for CameraX (blur)
send_tamper_alert triggered for CameraX (blur)
... (repeated every 3-5 seconds)
```

## Best Practices

### For Administrators

1. **Monitor Suppression Rates**: Check regularly which cameras are being suppressed
2. **Fix Root Causes**: Address network/camera issues rather than just acknowledging alerts
3. **Configure SMTP Early**: Set up SMTP before adding cameras to avoid initial flooding
4. **Test Notifications**: Use test alert feature to verify email delivery

### For Developers

1. **Always Use Circuit Breaker**: Wrap notification calls with failure recording
2. **Deduplicate Similar Alerts**: Use `should_send_tamper_alert()` for tamper detection
3. **Reset on Recovery**: Always call `reset_notification_suppression()` on camera recovery
4. **Early Exit**: Check `is_smtp_configured()` before attempting complex operations

## Related Documentation

- [Audit Logging](audit_logging.md) - Notification events are logged in audit trail
- [Security](security.md) - SMTP configuration and credential security
- [Deployment](deployment.md) - Email configuration during setup
