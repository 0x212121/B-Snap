# Security Policy

## Supported Versions

The following versions of B-Snap are currently supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 1.12.x  | :white_check_mark: |
| 1.11.x  | :white_check_mark: |
| 1.10.x  | :x:                |
| < 1.10  | :x:                |

## Reporting a Vulnerability

We take the security of B-Snap seriously. If you believe you have found a security vulnerability, please report it to us as described below.

### Please do NOT:

- **DO NOT** create a public GitHub issue for security vulnerabilities
- **DO NOT** disclose the vulnerability publicly before a fix is released
- **DO NOT** share details of the vulnerability with anyone other than the maintainers

### How to Report

**Email**: wijaya.indra2196@gmail.com

Please include the following information in your report:

1. **Description**: Clear description of the vulnerability
2. **Steps to Reproduce**: Detailed steps to reproduce the issue
3. **Impact**: Assessment of the potential impact
4. **Affected Versions**: Which versions are affected
5. **Proof of Concept**: If possible, include a proof of concept
6. **Your Contact**: How we can contact you for follow-up

### What to Expect

After you submit a vulnerability report:

1. **Acknowledgment**: We will acknowledge receipt of your report within 48 hours
2. **Assessment**: We will assess the vulnerability and determine its severity
3. **Updates**: We will keep you informed of our progress
4. **Resolution**: Once fixed, we will notify you and discuss coordinated disclosure
5. **Credit**: With your permission, we will credit you in the release notes

### Response Timeline

| Stage | Timeline |
|-------|----------|
| Initial Response | 48 hours |
| Vulnerability Assessment | 7 days |
| Fix Development | Depends on severity* |
| Fix Release | As soon as possible |
| Public Disclosure | After fix release |

*Severity-based timelines:*
- **Critical**: 7 days
- **High**: 30 days
- **Medium**: 90 days
- **Low**: Next scheduled release

## Security Best Practices

### For Administrators

When deploying B-Snap, please follow these security best practices:

1. **Use Strong Secrets**
   ```bash
   # Generate a strong SECRET_KEY
   openssl rand -hex 32
   ```

2. **Keep Software Updated**
   - Regularly update B-Snap to the latest version
   - Keep dependencies updated (`pip list --outdated`)
   - Monitor for security advisories

3. **Secure Database**
   - Use strong passwords for database users
   - Limit database network access
   - Enable SSL/TLS for database connections
   - Regular backups

4. **Network Security**
   - Use HTTPS in production
   - Place behind a reverse proxy (nginx/traefik)
   - Configure firewall rules
   - Use VPN for camera networks if possible

5. **Access Control**
   - Use strong passwords for all accounts
   - Enable 2FA for admin accounts
   - Regularly audit user access
   - Remove unused accounts

6. **Camera Security**
   - Change default camera passwords
   - Use separate network/VLAN for cameras if possible
   - Disable unused camera features
   - Keep camera firmware updated

7. **File Permissions**
   - Ensure proper file permissions on snapshot directories
   - Don't run as root
   - Use dedicated service account

### For Developers

When contributing to B-Snap:

1. **Dependency Management**
   - Run `safety check` before committing
   - Keep dependencies up to date
   - Pin dependency versions

2. **Code Security**
   - Never commit secrets or credentials
   - Use parameterized queries (SQLAlchemy ORM)
   - Validate all user inputs
   - Escape output in templates

3. **Testing**
   - Write tests for security-critical code
   - Run `bandit` security linter
   - Test authentication/authorization flows

## Security Features

B-Snap includes the following security features:

### Authentication
- ✅ Password-based authentication with bcrypt hashing
- ✅ Two-factor authentication (2FA) via TOTP
- ✅ Session management with secure cookies
- ✅ "Remember Me" functionality with secure tokens

### Authorization
- ✅ Role-based access control (RBAC)
- ✅ Resource-level permissions
- ✅ API endpoint protection

### Data Protection
- ✅ Password hashing (bcrypt)
- ✅ Sensitive data encryption at rest
- ✅ Secure session handling

### Network Security
- ✅ HTTPS support
- ✅ Secure cookie flags
- ✅ CSRF protection
- ✅ CORS configuration

### Audit & Monitoring
- ✅ Audit logging for sensitive operations
- ✅ Login/logout tracking
- ✅ Failed authentication tracking
- ✅ API access logging

## Security Hardening Checklist

Before deploying to production:

- [ ] Changed default SECRET_KEY
- [ ] Disabled DEBUG mode
- [ ] Configured HTTPS/TLS
- [ ] Set up reverse proxy
- [ ] Configured firewall
- [ ] Changed default admin password
- [ ] Enabled 2FA for admin accounts
- [ ] Configured database with strong password
- [ ] Enabled database SSL/TLS
- [ ] Set up log rotation
- [ ] Configured backup strategy
- [ ] Set up monitoring/alerting
- [ ] Reviewed camera credentials
- [ ] Tested disaster recovery

## Known Security Considerations

### Camera Credentials
- B-Snap stores camera credentials (username/password) in the database
- These are encrypted at rest
- Ensure database access is properly restricted

### Snapshot Storage
- Snapshots are stored on the filesystem
- Ensure proper file permissions are set
- Consider encryption for sensitive deployments

### Network Communication
- Camera communication may use HTTP (not HTTPS)
- Consider network segmentation for cameras
- Use VPN for remote camera access if possible

## Security Tools

We use the following tools for security:

- **Bandit**: Security linter for Python code
- **Safety**: Checks dependencies for known vulnerabilities
- **pip-audit**: Alternative dependency vulnerability scanner
- **GitHub Security Advisories**: Automated vulnerability alerts
- **Dependabot**: Automated dependency updates

## Security Updates

Security updates will be:
- Announced via GitHub Security Advisories
- Included in release notes
- Backported to supported versions when applicable

## Credits

We thank the following security researchers who have responsibly disclosed vulnerabilities:

*No security researchers have been credited yet. Be the first!*

## Contact

For security-related questions or concerns:

- **Email**: wijaya.indra2196@gmail.com
- **GitHub**: [@0x212121](https://github.com/0x212121)

---

Last Updated: 2024
