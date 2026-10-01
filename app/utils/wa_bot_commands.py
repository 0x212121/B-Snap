"""Configuration and matching helpers for native WhatsApp bot commands."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.config import Configuration
from app.utils.wa_bot_workflow import normalize_flow

CONFIG_KEY = "wa_bot_command_settings"

BUILTIN_COMMANDS: list[dict[str, Any]] = []

CUSTOM_ACTION_ROLES = {
    "text": "user",
    "help": "user",
    "ping_test": "user",
    "ping": "user",
    "cctv": "user",
    "snap": "user",
    "edit": "admin",
    "token_check": "admin",
    "whitelist_add": "admin",
    "whitelist_remove": "admin",
    "api_flow": "user",
}

PRIVATE_ONLY_ACTIONS = {"edit", "token_check", "whitelist_add", "whitelist_remove"}


def normalize_chat_scope(action: str, value: Any = None) -> str:
    """Return a safe chat scope; sensitive built-in actions always require a DM."""
    if action in PRIVATE_ONLY_ACTIONS:
        return "private"
    return value if isinstance(value, str) and value in {"all", "private"} else "all"


def get_command_settings(db: Session) -> list[dict[str, Any]]:
    row = db.query(Configuration).filter(Configuration.key == CONFIG_KEY).first()
    if row:
        try:
            configured = json.loads(row.value)
            if isinstance(configured, list):
                by_id = {item.get("id"): item for item in configured if isinstance(item, dict)}
                result = []
                for default in BUILTIN_COMMANDS:
                    item = {**default, **by_id.get(default["id"], {})}
                    # Built-in action and privilege requirements are server-owned.
                    item["action"] = default["action"]
                    item["role"] = default["role"]
                    item["chat_scope"] = normalize_chat_scope(item["action"], item.get("chat_scope"))
                    result.append(item)
                for item in configured:
                    if not isinstance(item, dict) or not str(item.get("id", "")).startswith("custom_"):
                        continue
                    action = item.get("action", "text")
                    if action not in CUSTOM_ACTION_ROLES:
                        continue
                    try:
                        flow = normalize_flow(item.get("flow")) if action == "api_flow" else []
                    except ValueError:
                        continue
                    result.append({**item, "action": action, "role": CUSTOM_ACTION_ROLES[action],
                                   "chat_scope": normalize_chat_scope(action, item.get("chat_scope")), "flow": flow})
                return result
        except (TypeError, ValueError):
            pass
    return [
        {**dict(item), "chat_scope": normalize_chat_scope(item["action"], item.get("chat_scope"))}
        for item in BUILTIN_COMMANDS
    ]


def save_command_settings(db: Session, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    defaults = {item["id"]: item for item in BUILTIN_COMMANDS}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in commands:
        if not isinstance(item, dict):
            raise ValueError("Setiap perintah harus berupa objek")
        command_id = str(item.get("id", ""))
        default = defaults.get(command_id)
        custom = command_id.startswith("custom_")
        if not default and not custom:
            raise ValueError("ID perintah tidak dikenal")
        if custom:
            action = str(item.get("action", "text"))
            if action not in CUSTOM_ACTION_ROLES:
                raise ValueError("Aksi command custom tidak didukung")
            entry = {"id": command_id, "name": str(item.get("name", "Perintah baru")), "trigger": str(item.get("trigger", "")), "aliases": item.get("aliases", []), "action": action, "role": CUSTOM_ACTION_ROLES[action], "enabled": item.get("enabled", True), "quote_reply": item.get("quote_reply", True), "description": str(item.get("description", "")), "response": str(item.get("response", ""))}
            if action == "api_flow":
                entry["flow"] = normalize_flow(item.get("flow"))
        else:
            entry = {**default, **item, "action": default["action"], "role": default["role"]}
        entry["name"] = str(entry.get("name", ""))[:80].strip()
        entry["trigger"] = str(entry.get("trigger", "")).strip().lower()[:80]
        entry["description"] = str(entry.get("description", ""))[:300].strip()
        entry["response"] = str(entry.get("response", ""))[:2000]
        entry["enabled"] = bool(entry.get("enabled", True))
        entry["quote_reply"] = bool(entry.get("quote_reply", True))
        entry["chat_scope"] = normalize_chat_scope(entry["action"], entry.get("chat_scope", item.get("chat_scope")))
        required_params = entry.get("required_params", item.get("required_params", []))
        if isinstance(required_params, str):
            required_params = re.split(r"[,\n]", required_params)
        if not isinstance(required_params, list) or len(required_params) > 10:
            raise ValueError("Parameter wajib harus berupa daftar maksimal 10 nama")
        normalized_params = []
        for parameter in required_params:
            parameter = str(parameter).strip()
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,39}", parameter):
                raise ValueError(f"Nama parameter tidak valid: {parameter}")
            if parameter in normalized_params:
                raise ValueError(f"Nama parameter duplikat: {parameter}")
            normalized_params.append(parameter)
        entry["required_params"] = normalized_params
        aliases = entry.get("aliases", [])
        if isinstance(aliases, str):
            aliases = re.split(r"[,\n]", aliases)
        entry["aliases"] = [str(alias).strip().lower()[:80] for alias in aliases if str(alias).strip()][:20]
        if not entry["name"] or not entry["trigger"]:
            raise ValueError("Nama dan trigger perintah wajib diisi")
        if entry["action"] != "ping" and not entry["response"].strip():
            raise ValueError("Template respons wajib diisi")
        if entry["enabled"]:
            for phrase in [entry["trigger"], *entry["aliases"]]:
                if phrase in seen:
                    raise ValueError(f"Trigger/alias duplikat: {phrase}")
                seen.add(phrase)
        normalized.append(entry)
    for default in BUILTIN_COMMANDS:
        if default["id"] not in {item["id"] for item in normalized}:
            normalized.append(dict(default, enabled=False))
    row = db.query(Configuration).filter(Configuration.key == CONFIG_KEY).first()
    value = json.dumps(normalized, ensure_ascii=False)
    if row:
        row.value = value
    else:
        db.add(Configuration(key=CONFIG_KEY, value=value))
    db.flush()
    return normalized


def match_command(message: str, commands: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    text = message.strip()
    lowered = text.lower()
    candidates = []
    for command in commands:
        if not command.get("enabled"):
            continue
        for phrase in [command.get("trigger", ""), *command.get("aliases", [])]:
            phrase = str(phrase).strip().lower()
            if phrase and (lowered == phrase or lowered.startswith(phrase + " ")):
                candidates.append((len(phrase), command, text[len(phrase):].strip()))
    if not candidates:
        return None, ""
    _, command, argument = max(candidates, key=lambda candidate: candidate[0])
    return command, argument


def render_response(template: str, **values: str) -> str:
    return re.sub(r"\{\{([a-z_]+)\}\}", lambda match: str(values.get(match.group(1), "")), template)
