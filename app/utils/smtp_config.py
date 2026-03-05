"""
SMTP Configuration Helper

Provides SMTP configuration with priority:
1. Database configuration (set via /config page)
2. Environment variables (fallback)
"""
import os
from app.core.config import get_config


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
    # Get from database first, fallback to env vars
    smtp_host = get_config("smtp_host") or os.getenv("SMTP_HOST", "")
    smtp_port = int(get_config("smtp_port") or os.getenv("SMTP_PORT", "587"))
    smtp_user = get_config("smtp_user") or os.getenv("SMTP_USER", "")
    smtp_pass = get_config("smtp_pass") or os.getenv("SMTP_PASS", "")
    email_from = get_config("email_from") or os.getenv("EMAIL_FROM", "") or smtp_user
    email_cc = get_config("email_cc") or os.getenv("EMAIL_CC", "")
    
    # Check if properly configured
    is_configured = bool(
        smtp_host and 
        smtp_user and 
        smtp_pass
    )
    
    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_pass": smtp_pass,
        "email_from": email_from or smtp_user,
        "email_cc": email_cc,
        "is_configured": is_configured
    }


def is_smtp_configured():
    """
    Quick check if SMTP is properly configured.
    Returns True if host, user, and password are set.
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
        if not config["smtp_user"]:
            missing.append("SMTP Username")
        if not config["smtp_pass"]:
            missing.append("SMTP Password")
        
        return {
            "configured": False,
            "message": f"SMTP not configured. Missing: {', '.join(missing)}",
            "level": "warning",
            "missing": missing
        }
