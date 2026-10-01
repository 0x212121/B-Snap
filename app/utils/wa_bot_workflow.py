"""Safe, sequential HTTP GET workflows used by WhatsApp bot commands."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import os
import pytz

MAX_FLOW_STEPS = 8
MAX_RESPONSE_BYTES = 1_000_000
STEP_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
FLOW_VARIABLE_PATTERN = re.compile(
    r"\{\{(argument|sender|steps\.[a-zA-Z][a-zA-Z0-9_]{0,39}\.(?:status_code|body(?:\.[a-zA-Z0-9_-]+)*)|this(?:\.[a-zA-Z0-9_-]+)*|@index1|@index)\}\}"
)
FLOW_DATETIME_VARIABLE_PATTERN = re.compile(
    r"\{\{(?P<expression>(?:argument|sender|steps\.[a-zA-Z][a-zA-Z0-9_]{0,39}\."
    r"(?:status_code|body(?:\.[a-zA-Z0-9_-]+)*)|this(?:\.[a-zA-Z0-9_-]+)*|@index1|@index))"
    r"\|datetime\}\}"
)
FLOW_EACH_PATTERN = re.compile(
    r"\{\{#each\s+(steps\.[a-zA-Z][a-zA-Z0-9_]{0,39}\.(?:status_code|body(?:\.[a-zA-Z0-9_-]+)*))\s*\}\}"
    r"(.*?)\{\{/each\}\}",
    re.DOTALL,
)
MAX_FLOW_TEMPLATE_ITEMS = 50
MAX_RENDERED_RESPONSE_CHARS = 10_000


def normalize_flow(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_FLOW_STEPS:
        raise ValueError(f"Workflow harus berisi 1 sampai {MAX_FLOW_STEPS} node API")
    normalized = []
    names = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Setiap node workflow harus berupa objek")
        name = str(item.get("name", "")).strip()
        path = str(item.get("path", "")).strip()
        if not STEP_NAME_PATTERN.fullmatch(name):
            raise ValueError("Nama node hanya boleh berisi huruf, angka, dan underscore; awali dengan huruf")
        if name in names:
            raise ValueError(f"Nama node workflow duplikat: {name}")
        names.add(name)
        if not path.startswith("/") or path.startswith("//") or "\\" in path or "://" in path:
            raise ValueError(f"Path node {name} harus berupa path relatif yang diawali /")
        params = item.get("params", {})
        if isinstance(params, str):
            try:
                params = json.loads(params or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Query params node {name} harus JSON object yang valid") from exc
        if not isinstance(params, dict) or len(params) > 50:
            raise ValueError(f"Query params node {name} harus berupa JSON object (maksimal 50 item)")
        normalized.append({"name": name, "path": path[:500], "params": params})
    return normalized


def _resolve_expression_value(expression: str, context: dict[str, Any]) -> Any:
    if expression in {"argument", "sender"}:
        return context.get(expression, "")
    if expression == "@index":
        return context.get("@index", "")
    if expression == "@index1":
        return context.get("@index1", "")
    if expression == "this":
        return context.get("this", "")
    parts = expression.split(".")
    if parts[0] == "this":
        value: Any = context.get("this")
        path = parts[1:]
    else:
        _, step_name, *path = parts
        value = context.get("steps", {}).get(step_name)
    for key in path:
        if key == "status_code" and isinstance(value, dict):
            value = value.get(key, "")
        elif key == "body" and isinstance(value, dict):
            value = value.get("body", "")
        elif isinstance(value, dict):
            value = value.get(key, "")
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            value = ""
    return value


def _resolve_expression(expression: str, context: dict[str, Any]) -> str:
    value = _resolve_expression_value(expression, context)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "")


def _format_datetime_value(value: Any, timezone_name: str) -> str:
    """Normalize an ISO/database timestamp into the application's configured timezone."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw_value = value.strip()
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return _resolve_expression_value_to_string(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        local_time = parsed.astimezone(pytz.timezone(timezone_name))
    except (pytz.UnknownTimeZoneError, ValueError):
        local_time = parsed.astimezone(pytz.UTC)
    return local_time.strftime("%d/%m/%Y - %H:%M:%S %Z")


def _resolve_expression_value_to_string(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "")


def _render_template_variables(template: str, context: dict[str, Any], timezone_name: str) -> str:
    rendered = FLOW_DATETIME_VARIABLE_PATTERN.sub(
        lambda match: _format_datetime_value(
            _resolve_expression_value(match.group("expression"), context), timezone_name
        ),
        template,
    )
    return FLOW_VARIABLE_PATTERN.sub(
        lambda match: _resolve_expression(match.group(1), context), rendered
    )


def render_flow_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return FLOW_VARIABLE_PATTERN.sub(lambda match: _resolve_expression(match.group(1), context), value)
    if isinstance(value, list):
        return [render_flow_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render_flow_value(item, context) for key, item in value.items()}
    return value


async def run_api_flow(
    *,
    flow: list[dict[str, Any]],
    bearer_token: str | None,
    sender: str,
    argument: str,
) -> dict[str, Any]:
    """Call this B-Snap instance's GET API nodes sequentially."""
    headers = {"Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    context: dict[str, Any] = {"sender": sender, "argument": argument, "steps": {}}
    timeout = httpx.Timeout(15.0, connect=5.0)
    raw_hosts = os.getenv("TRUSTED_HOSTS", "*")
    trusted_host = next((host.strip() for host in raw_hosts.split(",") if host.strip() and host.strip() != "*"), "localhost")
    transport = httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{trusted_host}",
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
    ) as client:
        for step in flow:
            path = render_flow_value(step["path"], context)
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "://" in path:
                raise ValueError(f"Path hasil template tidak valid pada node {step['name']}")
            if re.search(r"token|password|secret|credential", path, re.IGNORECASE):
                raise ValueError(f"Path node {step['name']} tidak boleh meminta kredensial atau token")
            params = render_flow_value(step.get("params", {}), context)
            response = await client.get(path, params=params)
            if response.is_redirect:
                raise ValueError(f"API node {step['name']} memerlukan token API internal B-Snap yang valid")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ValueError(f"Respons API node {step['name']} melebihi batas 1 MB")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body_preview = response.text[:300].strip()
                suffix = f": {body_preview}" if body_preview else ""
                raise ValueError(
                    f"API node {step['name']} mengembalikan HTTP {response.status_code}{suffix}"
                ) from exc
            try:
                body: Any = response.json()
            except (ValueError, json.JSONDecodeError):
                body = response.text[:20_000]
            context["steps"][step["name"]] = {"status_code": response.status_code, "body": body}
    return context


def render_flow_template(
    template: str,
    context: dict[str, Any],
    timezone_name: str = "UTC",
) -> str:
    def render_each(match: re.Match[str]) -> str:
        collection = _resolve_expression_value(match.group(1), context)
        if not isinstance(collection, list):
            return ""
        rendered_items = []
        for index, item in enumerate(collection[:MAX_FLOW_TEMPLATE_ITEMS]):
            item_context = {**context, "this": item, "@index": index, "@index1": index + 1}
            rendered_items.append(
                _render_template_variables(match.group(2), item_context, timezone_name)
            )
        return "".join(rendered_items)

    rendered = FLOW_EACH_PATTERN.sub(render_each, template)
    rendered = _render_template_variables(rendered, context, timezone_name)
    return rendered[:MAX_RENDERED_RESPONSE_CHARS]
