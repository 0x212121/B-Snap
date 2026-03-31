# Security

B-Snap implements defense-in-depth security for CCTV environments, addressing both cyber threats and physical evidence protection requirements.

## Authentication & Authorization

### Session-Based Authentication
- FastAPI session middleware with secure cookie settings
- Passwords hashed with bcrypt (adaptive hashing)
- Session timeout after 30 minutes of inactivity
- Role-based access control (Admin, Operator, Viewer)

### One-Time Password (OTP)
- TOTP-based two-factor authentication
- QR code enrollment for authenticator apps
- Backup codes for account recovery

## Data Protection

### Password Encryption (P1-001)
Camera and NVR passwords are encrypted at rest using **AES-256-GCM**:

```text
# Encryption flow
plaintext password -> AES-256-GCM -> ciphertext stored in DB
```

- **Algorithm**: AES-256-GCM (authenticated encryption)
- **Key Management**: Environment variable `ENCRYPTION_KEY`
- **Key Generation**: `openssl rand -hex 32`
- **Backward Compatible**: Existing plain-text passwords migrated on first save

### API Security (P1-002)
- Passwords excluded from standard API responses
- Admin-only endpoints for password retrieval:
  - `GET /api/camera/{id}/password`
  - `GET /api/nvr/{id}/password`
- All password access logged in audit trail

### File Integrity (P0-001)
SHA-256 hashes for all snapshots and videos:

```python
# Hash calculation on file creation
file_hash = hashlib.sha256(file_content).hexdigest()
```

- **Verification API**: `GET /snap/{id}/verify`
- **Tamper Detection**: Hash mismatch indicates file modification
- **Evidence Integrity**: Hash stored in database alongside file path

## Audit & Compliance (P2)

### Append-Only Audit Log (P2-001)

Database-level enforcement prevents tampering:

```text
-- Triggers prevent modification
BEFORE DELETE ON audit_logs -> RAISE EXCEPTION
BEFORE UPDATE ON audit_logs -> RAISE EXCEPTION
```

**Log Retention Strategy**:
- **Hot Storage** (0-6 months): PostgreSQL `audit_logs` table
- **Warm Storage** (6+ months): `audit_logs_legacy` table
- **Cold Storage** (archived): Encrypted files in `archives/audit_logs/`

**Archive Process**:
1. Export to JSON
2. Gzip compression (~90% reduction)
3. AES-256 encryption
4. SHA-256 checksum verification
5. Safe deletion from hot storage

See [Audit Logging](audit_logging.md) for detailed documentation.

### Safety Classification (P2-002)

Cameras classified by criticality:

| Level | Badge | Use Case |
|-------|-------|----------|
| 🔴 Critical | Red | Incident coverage, entry points, high-risk areas |
| 🟡 Standard | Yellow | Regular monitoring, general surveillance |
| 🟢 Low | Green | Peripheral areas, low-sensitivity zones |

**Impact**: Critical cameras highlighted in UI and have priority in retention policies.

### Retention Hold (P2-003)

Legal hold mechanism for incident footage:

```python
snapshot.retention_hold = True
snapshot.retention_hold_reason = "Investigation Case #2024-001"
```

- Prevents automatic purge of incident-related snapshots
- Requires admin role to set/clear
- All retention hold operations audited
- Visual indicator in snapshot gallery

### Secure File Serving (P2-004)

Direct static file access blocked:

```
GET /static/snapshots/camera1/2024-01-01/image.jpg
→ 403 Forbidden
```

**Secure Access** (authenticated):
```
GET /api/snapshots/secure/{snapshot_id}
→ 200 OK (after auth check)
```

Benefits:
- All file access logged
- Authentication enforced
- No direct filesystem exposure

## OWASP Top 10 Mitigations

| Risk | Mitigation |
|------|------------|
| **Injection** | SQLAlchemy ORM + parameterized queries |
| **Broken Auth** | bcrypt hashing, secure cookies, session expiry, OTP |
| **Sensitive Data** | AES-256 encryption, HTTPS required, password masking |
| **XXE** | No XML parsing in application |
| **Access Control** | Role-based permissions, admin-only endpoints |
| **Security Misconfig** | Environment-based config, no debug in production |
| **XSS** | Jinja2 autoescape, Content-Security-Policy headers |
| **Insecure Deserialization** | JSON only, no pickle/xml deserialization |
| **Components** | Regular dependency updates, CVE monitoring |
| **Logging** | Comprehensive audit logging, append-only protection |

## Network Security

### Camera Communication
- ONVIF/RTSP with digest authentication
- Passwords encrypted at rest and in transit
- Isolated VLAN for CCTV cameras (recommended)

### Web Application
- HTTPS enforced (TLS 1.2+)
- Secure cookie flags (HttpOnly, Secure, SameSite)
- CORS configured for same-origin only
- Rate limiting on authentication endpoints

## Evidence Protection

### Soft Delete (P0-002)
Deleted snapshots are recoverable:

```python
# Soft delete (default)
snapshot.deleted_at = datetime.now()

# Restore (admin only)
snapshot.deleted_at = None

# Hard delete (admin + audit logged)
db.delete(snapshot)  # Only after soft delete
```

**Benefits**:
- Prevents accidental deletion
- Recovery within retention period
- Audit trail of all deletions

### Chain of Custody
Every snapshot access creates audit record:

| Field | Example |
|-------|---------|
| User | john.doe |
| Action | download_snapshot |
| Target | snapshot_12345.jpg |
| IP | 192.168.1.50 |
| Timestamp | 2024-03-28T10:30:00Z |
| User Agent | Mozilla/5.0... |
| Request Path | /api/snapshots/secure/12345 |

### Tamper Detection
Health monitoring detects camera tampering:

```
CameraHealth.tamper_detected = True
CameraHealth.tamper_count += 1
```

Triggers:
- Unexpected reboot
- Configuration changes
- Network anomalies

## Compliance

### ISO 27001
- **A.12.4** - Logging and monitoring ✅
- **A.10.1** - Cryptographic controls ✅
- **A.9.4** - System access control ✅

### SOC 2 Type II
- **CC6.x** - Logical and physical access ✅
- **CC7.x** - System operations and monitoring ✅

### CCTV Evidence Standards
- Chain of custody maintained
- Tamper-evident logging
- Configurable retention periods
- Legal hold capability

## Security Checklist

### Deployment
- [ ] Change default admin password
- [ ] Set strong `ENCRYPTION_KEY` (32-byte hex)
 [ ] Configure `SECRET_KEY` for sessions
- [ ] Enable HTTPS
- [ ] Set `ENVIRONMENT=production`
- [ ] Disable debug mode

### Maintenance
- [ ] Review audit logs weekly
- [ ] Archive old logs (6-month cycle)
- [ ] Update dependencies monthly
- [ ] Backup encryption keys securely
- [ ] Test restore from archive

### Monitoring
- [ ] Failed login attempts
- [ ] Password access events
- [ ] Unusual download patterns
- [ ] Tamper alerts
- [ ] Archive job status

## Incident Response

### Suspected Breach
1. Check audit logs for unauthorized access
2. Verify file integrity hashes
3. Review retention hold settings
4. Contact security team

### Audit Log Compromise
1. Archives are immutable (checksums)
2. Restore from encrypted backups
3. Verify chain of custody intact

## Additional Resources

- [Audit Logging Documentation](audit_logging.md)
- [Deployment Guide](deployment.md)
- [API Security Reference](openapi.json)
