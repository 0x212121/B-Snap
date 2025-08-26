# Security

## Authentication
- FastAPI session middleware with secure cookie.
- Passwords hashed with bcrypt.

## OWASP Considerations
- **Injection**: ORM (SQLAlchemy) + parameterized queries.
- **Broken Auth**: Secure cookie, session expiry, and OTP.
- **Sensitive Data Exposure**: HTTPS required.
- **XSS**: Jinja2 autoescape enabled.
- **CSRF**: Not required (API-only), JWT used.
