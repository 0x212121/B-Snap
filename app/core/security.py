"""Shared process security settings, validated before accepting application traffic."""

from __future__ import annotations

import os

from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_network
from urllib.parse import urlsplit


def _boolean(values: Mapping[str, str], key: str, default: bool) -> bool:
    value = values.get(key, str(default)).lower().strip()
    if value not in {"true", "1", "yes", "false", "0", "no"}:
        raise ValueError(f"{key} must be a boolean")
    return value in {"true", "1", "yes"}


def origin_key(value: str) -> tuple[str, str, int]:
    """Normalize a serialized HTTP origin without accepting credentials or paths."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid HTTP origin")
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


@dataclass(frozen=True)
class SecuritySettings:
    """Security policy shared by web sessions and persistent login cookies."""

    environment: str
    secret_key: str = field(repr=False)
    cookie_secure: bool
    cookie_samesite: str
    trusted_proxies: tuple[str, ...]
    public_origin: str | None


def load_security_settings(values: Mapping[str, str] | None = None) -> SecuritySettings:
    """Fail closed on unsafe production settings without disclosing their values."""
    values = os.environ if values is None else values
    environment = values.get("ENVIRONMENT", "development").lower().strip()
    if environment not in {"development", "testing", "production"}:
        raise ValueError("ENVIRONMENT must be development, testing, or production")
    production = environment == "production"
    secret = values.get("SECRET_KEY", "your-default-secret-key-for-dev")
    known_defaults = {
        "your-default-secret-key-for-dev",
        "dev-secret",
        "secret",
        "changeme",
        "your-very-secret-key-change-this-in-production",
    }
    if production and (len(secret) < 32 or secret.lower() in known_defaults or not secret.strip()):
        raise ValueError("Production requires a private SECRET_KEY of at least 32 characters")
    behind_proxy = _boolean(values, "BEHIND_HTTPS_PROXY", False)
    secure = _boolean(values, "COOKIE_SECURE", production or behind_proxy)
    same_site = values.get("COOKIE_SAMESITE", "lax").lower().strip()
    if same_site not in {"lax", "strict", "none"}:
        raise ValueError("COOKIE_SAMESITE must be lax, strict, or none")
    if (production or same_site == "none") and not secure:
        raise ValueError("Secure cookies are required for production and SameSite=None")
    proxies = _trusted_proxies(values, production, behind_proxy)
    public_origin = values.get("PUBLIC_ORIGIN", "").strip() or None
    if public_origin:
        key = origin_key(public_origin)
        if production and key[0] != "https":
            raise ValueError("Production PUBLIC_ORIGIN must use HTTPS")
    return SecuritySettings(environment, secret, secure, same_site, proxies, public_origin)


def _trusted_proxies(
    values: Mapping[str, str], production: bool, behind_proxy: bool
) -> tuple[str, ...]:
    # Legacy TRUSTED_HOSTS was used for proxy trust, not CORS. Production requires
    # an explicit IP/CIDR setting; an implicit wildcard is never accepted.
    raw = values.get("TRUSTED_PROXY_HOSTS")
    if raw is None:
        raw = "" if production else values.get("TRUSTED_HOSTS", "127.0.0.1,::1")
    proxies = tuple(part.strip() for part in raw.split(",") if part.strip())
    if production:
        if behind_proxy and not proxies:
            raise ValueError("BEHIND_HTTPS_PROXY requires explicit TRUSTED_PROXY_HOSTS")
        try:
            for proxy in proxies:
                if ip_network(proxy, strict=False).prefixlen == 0:
                    raise ValueError("Wildcard proxy network")
        except ValueError as exc:
            raise ValueError("Production TRUSTED_PROXY_HOSTS must contain proxy IPs/CIDRs") from exc
    return proxies
