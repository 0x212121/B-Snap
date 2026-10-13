"""Conditional command responses use the same renderer as previews and delivery."""

from __future__ import annotations

import pytest

from app.utils.wa_bot_workflow import render_flow_template


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full database setup because template rendering does not use a DB."""


@pytest.mark.parametrize("ip", [None, "", "   ", "192.168.1.10"])
def test_optional_ip_inside_each(ip: str | None) -> None:
    template = (
        "{{#each steps.result.body}}"
        "{{this.camera}}{{#if this.ip}} ({{this.ip}}){{/if}}\n"
        "Time: {{this.timestamp|datetime}}\nUptime: {{this.uptime}}\n"
        "{{/each}}"
    )
    context = {
        "steps": {
            "result": {
                "body": [
                    {
                        "camera": "Gate",
                        "ip": ip,
                        "timestamp": "2026-10-12T16:00:00Z",
                        "uptime": "1h",
                    },
                    {"camera": "Other", "ip": "192.168.1.20", "uptime": "2h"},
                ]
            }
        }
    }
    output = render_flow_template(template, context, "Asia/Makassar")
    name = f"Gate ({ip})" if ip and ip.strip() else "Gate"
    assert output.startswith(name + "\nTime: 13/10/2026 - 00:00:00 WITA\nUptime: 1h\n")
    assert "Other (192.168.1.20)" in output
    assert "{{" not in output


@pytest.mark.parametrize("value", [None, "", False, 0, [], {}, "yes", 1])
def test_if_else_and_nested_conditions(value: object) -> None:
    context = {"steps": {"result": {"body": {"value": value, "ip": "192.168.1.10"}}}}
    template = (
        "{{#if steps.result.body.value}}"
        "IP: {{#if steps.result.body.ip}}{{steps.result.body.ip}}{{else}}missing{{/if}}"
        "{{else}}empty{{/if}}"
    )
    assert render_flow_template(template, context) == ("IP: 192.168.1.10" if value else "empty")


def test_missing_value_and_unsupported_expression() -> None:
    template = "{{#if this.missing}}hidden{{/if}}{{#if bad()}}hidden{{/if}}OK"
    assert render_flow_template(template, {}) == "OK"
