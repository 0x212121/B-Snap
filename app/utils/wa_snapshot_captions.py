"""Shared per-image caption rendering for camera batch actions and previews."""

from __future__ import annotations

from typing import Any

from app.utils.wa_bot_commands import render_response
from app.utils.wa_bot_workflow import render_flow_template


def render_snapshot_caption(
    template: str,
    context: dict[str, Any],
    item: dict[str, Any],
    timezone_name: str,
    *,
    index: int = 0,
) -> str:
    """Keep flat fields and mapper loops available for only the current image."""
    steps = dict(context.get("steps") or {})
    result = dict(steps.get("result") or {})
    body = result.get("body")
    body = body if isinstance(body, dict) else {}
    result["body"] = {**body, **item, "results": [item]}
    steps["result"] = result
    caption_context = {
        **context,
        "steps": steps,
        "this": item,
        "@index": index,
        "@index1": index + 1,
        "_each_offsets": {"steps.result.body.results": index},
    }
    template = template or "{{steps.result.body.camera}}\n{{steps.result.body.timestamp|datetime}}"
    return render_response(
        render_flow_template(template, caption_context, timezone_name),
        **item,
        argument=context.get("argument", ""),
        sender=context.get("sender", ""),
    )
