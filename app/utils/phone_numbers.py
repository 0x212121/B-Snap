"""Parse user-supplied telephone numbers without changing legacy records."""

from __future__ import annotations

import re

import phonenumbers


def normalize_phone_number(value: str, region: str = "ID") -> str:
    """Return a valid E.164 number, interpreting local input in the selected region.

    An explicit international prefix takes precedence. Extensions are rejected.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Phone number is required")
    if not isinstance(region, str) or region.upper() not in phonenumbers.SUPPORTED_REGIONS:
        raise ValueError("Invalid phone region")
    try:
        number = phonenumbers.parse(value.strip(), region.upper())
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Invalid phone number") from exc
    if number.extension or not phonenumbers.is_valid_number(number):
        raise ValueError("Invalid phone number")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_whatsapp_phone(value: str, region: str = "ID") -> str:
    """Validate a WhatsApp number and return E.164.

    Unprefixed nonzero input follows the legacy digit-only international format
    used by whitelist exports and GoWA; local input uses the selected region.
    """
    value = value.strip()
    if value and value[0].isdigit() and value[0] != "0":
        value = "+" + value
    return normalize_phone_number(value, region)


def legacy_whatsapp_lookup(value: str) -> str | None:
    """Normalize a lookup without requiring historical numbers to pass current metadata.

    Reject letters, extensions and malformed plus signs instead of silently
    stripping them from an authorization identity. A local leading zero follows
    the pre-existing Indonesian webhook convention.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9][0-9 ()-]*", value):
        return None
    digits = re.sub(r"[ ()-]", "", value).removeprefix("+")
    if value.startswith("+") and digits.startswith("0"):
        return None
    return "62" + digits[1:] if digits.startswith("0") else digits


def whatsapp_phone_variants(value: str) -> tuple[str, ...]:
    """Return the legacy and canonical database keys for a supported identity."""
    digits = legacy_whatsapp_lookup(value)
    return (digits, "+" + digits) if digits else ()
