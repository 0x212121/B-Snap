"""Persistent GoWA device outage detection and administrator email alerts."""

from __future__ import annotations

import json
import logging
import re

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy.orm import Session

from app.models.config import Configuration
from app.utils.email_helper import send_email
from app.utils.timezone_helper import format_datetime_standard
from app.utils.wa_gateway import WAGatewayService

logger = logging.getLogger(__name__)
STATE_KEY = "gowa_monitor_state"
CONFIRMATIONS = 2


def parse_monitor_recipients(
    value: str, *, enabled: bool = False, gateway_enabled: bool = True
) -> list[str]:
    """Validate a comma-separated administrator recipient list."""
    recipients = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if any(
        not re.fullmatch(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", address)
        for address in recipients
    ):
        raise ValueError("Invalid GoWA alert email recipients.")
    if enabled and (not gateway_enabled or not recipients):
        raise ValueError("Enable WhatsApp and enter GoWA alert email recipients first.")
    return recipients


def device_status(connection: dict) -> str:
    """Distinguish a lost login from a disconnected or unreachable gateway."""
    if connection.get("error"):
        return "unreachable"
    if connection.get("logged_in") is False:
        return "logged_out"
    if connection.get("connected") is True:
        return "healthy"
    return "disconnected"


def check_gowa_device(db: Session) -> dict:
    """Send one email per confirmed outage, retaining state across worker restarts.

    The state row is locked until delivery and commit so concurrent checks cannot
    normally send the same incident twice. SMTP failures retain the original
    incident time and are retried by the next check. A healthy check rearms alerts.
    """
    service = WAGatewayService(db)
    config = service.config
    row = db.query(Configuration).filter_by(key=STATE_KEY).with_for_update().first()
    if row is None:
        row = Configuration(key=STATE_KEY, value="{}")
        db.add(row)
        db.flush()
    try:
        state = json.loads(row.value or "{}")
        if not isinstance(state, dict):
            state = {}
    except (TypeError, ValueError):
        state = {}

    if config.get("gowa_monitor_enabled", "0") != "1" or not config.is_configured():
        row.value = "{}"
        db.commit()
        return {"status": "disabled"}

    recipients = parse_monitor_recipients(config.get("gowa_monitor_recipients", ""))
    if not recipients:
        raise ValueError("GoWA alert email recipients are required.")
    identity = sha256(
        json.dumps([config.base_url, config.device_id, config.api_key]).encode()
    ).hexdigest()
    if state.get("identity") != identity:
        state = {"identity": identity}
    status = device_status(service.check_connection())
    if status == "healthy":
        state = {"identity": identity}
    else:
        # Keep a single incident even if disconnected later becomes logged out.
        state.setdefault("incident_time", datetime.now(UTC).isoformat())
        state["failures"] = min(int(state.get("failures", 0)) + 1, CONFIRMATIONS)
        state["status"] = status
    row.value = json.dumps(state)

    if status != "healthy" and state["failures"] >= CONFIRMATIONS and not state.get("sent"):
        descriptions = {
            "logged_out": "Device logged off. Pair the device again in GoWA.",
            "disconnected": "Device disconnected from WhatsApp. Check its connection in GoWA.",
            "unreachable": "B-Snap cannot obtain device status from GoWA. Check gateway connectivity and configuration.",
        }
        incident_time = datetime.fromisoformat(state["incident_time"])
        body = (
            "B-Snap WhatsApp device alert\n\n"
            f"Device ID: {config.device_id or '(default device)'}\n"
            f"Status: {status}\n"
            f"First detected: {format_datetime_standard(incident_time, db)}\n\n"
            f"{descriptions[status]}\n"
            "The WhatsApp bot may be unable to respond or deliver notifications.\n"
            "One alert is sent per outage; alerts resume after a healthy check."
        )
        try:
            send_email(recipients, f"[B-Snap] WhatsApp device {status}", body, high_importance=True)
        except Exception:
            # Do not include transport exceptions, which may contain credentials.
            db.commit()
            logger.warning("GoWA device alert email failed; retrying on the next check")
            raise RuntimeError("GoWA device alert email failed; pending retry") from None
        state["sent"] = True
        row.value = json.dumps(state)
    db.commit()
    return {"status": status, "alert_sent": bool(state.get("sent"))}
