"""
SMTP Configuration Helper

Provides SMTP configuration with priority:
1. Database configuration (set via /config page)
2. Environment variables (fallback)
"""
import os

from app.core.config import get_config
from app.utils.email_retry_runtime import current_email_retry_runtime

SMTP_SECURITY_OPTIONS = {"starttls", "ssl_tls", "none"}


def _get_smtp_security() -> str:
    """Return a supported SMTP security mode, preserving legacy settings."""
    security = (
        get_config("smtp_security") or os.getenv("SMTP_SECURITY", "starttls")
    ).strip().lower()
    if security not in SMTP_SECURITY_OPTIONS:
        # Existing installations had no security setting and always used STARTTLS.
        return "starttls"
    return security


def get_smtp_config():
    """
    Get SMTP configuration with fallback to environment variables.

    Returns dict with:
    - smtp_host
    - smtp_port
    - smtp_user
    - smtp_pass
    - email_from
    - email_cc
    - is_configured (bool)
    """
    runtime = current_email_retry_runtime()
    if runtime is not None and runtime.smtp_config is not None:
        return runtime.smtp_config

    # Get from database first, fallback to env vars
    smtp_host = get_config("smtp_host") or os.getenv("SMTP_HOST", "")
    try:
        smtp_port = int(get_config("smtp_port") or os.getenv("SMTP_PORT", "587"))
    except (TypeError, ValueError):
        smtp_port = 587
    smtp_user = get_config("smtp_user") or os.getenv("SMTP_USER", "")
    smtp_pass = get_config("smtp_pass") or os.getenv("SMTP_PASS", "")
    email_from = get_config("email_from") or os.getenv("EMAIL_FROM", "") or smtp_user
    email_cc = get_config("email_cc") or os.getenv("EMAIL_CC", "")
    smtp_security = _get_smtp_security()
    has_credentials = bool(smtp_user and smtp_pass)

    # Check if properly configured
    is_configured = bool(
        smtp_host and email_from and (has_credentials or not smtp_user and not smtp_pass)
    )

    config = {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_pass": smtp_pass,
        "email_from": email_from or smtp_user,
        "email_cc": email_cc,
        "smtp_security": smtp_security,
        "has_credentials": has_credentials,
        "is_configured": is_configured,
    }
    if runtime is not None:
        runtime.smtp_config = config
    return config


def is_smtp_configured():
    """
    Quick check if SMTP is properly configured.
    Returns True if host and sender are set, with either both SMTP credentials or none.
    """
    config = get_smtp_config()
    return config["is_configured"]


def get_smtp_status_message():
    """
    Get human-readable status message for SMTP configuration.
    """
    config = get_smtp_config()
    
    if config["is_configured"]:
        return {
            "configured": True,
            "message": f"SMTP configured ({config['smtp_host']}:{config['smtp_port']})",
            "level": "success"
        }
    else:
        missing = []
        if not config["smtp_host"]:
            missing.append("SMTP Host")
        if not config["email_from"]:
            missing.append("From Email")
        if bool(config["smtp_user"]) != bool(config["smtp_pass"]):
            missing.append("both SMTP Username and Password (or leave both empty)")
        
        return {
            "configured": False,
            "message": f"SMTP not configured. Missing: {', '.join(missing)}",
            "level": "warning",
            "missing": missing
        }
