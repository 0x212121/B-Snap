"""Rendering and safety checks for the shared UI and representative workflows."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from starlette.requests import Request
from starlette.routing import Route, Router

from app.utils.template_helper import templates


@pytest.mark.parametrize("scenario", ["idle", "running_on_open", "fast_ping_all"])
def test_health_refreshes_only_on_load_or_check_completion(scenario: str) -> None:
    """Execute the page script with isolated timers and health-check responses."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the frontend regression check")

    source = (Path(__file__).resolve().parents[2] / "templates" / "health.html").read_text(
        encoding="utf-8"
    )
    script = templates.env.from_string(
        '{% import "_icons.html" as ui_icons %}'
        '{% from "_icons.html" import icon_arrow_path, icon_camera, icon_play %}'
        + "\n".join(re.findall(r"<script>(.*?)</script>", source, re.S))
    ).render(server_timezone="Asia/Makassar", server_timezone_label="WITA")
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {script, scenario} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let ready, poll, refreshes = 0, triggerRequests = 0;
const elements = new Map();
const context = vm.createContext({
  console,
  document: {
    addEventListener(event, callback) { if (event === 'DOMContentLoaded') ready = callback; },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {
        style: {}, classList: {add() {}, remove() {}}, disabled: false, innerHTML: ''
      });
      return elements.get(id);
    }
  },
  window: {addEventListener() {}},
  setInterval(callback) { poll = callback; return 1; },
  clearInterval() { poll = null; },
  setTimeout(callback) { callback(); },
  hideGlobalConfirm() {},
  fetch: async (url) => {
    if (url === '/health/trigger_all') triggerRequests++;
    return {ok: true, json: async () => ({is_complete: true})};
  }
});
vm.runInContext(script, context);
context.refreshTable = () => { refreshes++; };
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  ready();
  assert.equal(refreshes, 1, 'Opening the page loads the table once');
  if (scenario === 'running_on_open') {
    context.fetch = async () => ({ok: true, json: async () => ({
      is_complete: false, progress: {total: 2, completed: 1}
    })});
    poll();
    await settle();
    assert.equal(refreshes, 1, 'An active check does not reload the table yet');
  } else {
    poll();
    await settle();
    assert.equal(refreshes, 1, 'Idle completion must not reload the table');
    assert.equal(poll, null, 'Idle polling stops');
  }
  context.fetch = async (url) => {
    if (url === '/health/trigger_all') triggerRequests++;
    return {ok: true, json: async () => ({is_complete: true})};
  };
  if (scenario === 'fast_ping_all') {
    context.executePingAll();
    await settle();
    assert.equal(triggerRequests, 1);
  }
  if (scenario !== 'idle') {
    poll();
    await settle();
    assert.equal(refreshes, 2, 'A completed active check reloads the table once');
    assert.equal(poll, null, 'Completed polling stops');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"script": script, "scenario": scenario}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Presentation checks do not require the application's database fixture."""


class PageParser(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    ("page", "required_ids"),
    [
        ("analytics", {"analyticsGroupFilter", "startDate", "endDate", "downloadReport"}),
        (
            "cameras",
            {
                "searchInput",
                "camera-table",
                "cameraForm",
                "csvFileInput",
                "locationFilter",
                "safetyFilter",
                "statusFilter",
            },
        ),
        (
            "health",
            {
                "searchInput",
                "healthCheckTable",
                "typeFilter",
                "statusFilter",
                "perPageSelect",
                "ping-all-btn",
                "healthCheckStatus",
            },
        ),
        ("user_profile", {"tab-password", "tab-api_keys", "changePasswordForm"}),
        ("change_password", {"current_password", "new_password", "confirm_password"}),
        ("config", {"configForm", "configTabBar", "config-tab-general", "saveButton"}),
        ("wa_bot_builder", {"commandActions", "commandsTab", "messagesPanel", "emptyAddCommand"}),
        ("snapshot_gallery", {"searchInput", "cameraSelect", "clearFilterBtn"}),
        ("video_gallery", {"searchInput", "cameraSelect", "clearFilterBtn"}),
        ("docs/environment", set()),
        ("maps", {"map"}),
        ("nvrs", {"nvr-table", "csvFileInput", "perPageSelect"}),
        ("camera_groups", {"loading-overlay", "stat-total"}),
        ("record_checks", {"sources-list", "sum-healthy"}),
        ("powerbi_record_checks", set()),
        ("jobs", {"stat-total-runs"}),
        ("whitelist", {"whitelist-table", "stat-total"}),
        ("recipients", {"recipients-table", "stat-total"}),
        ("email_templates", {"templateTabs", "variablesList"}),
        ("user_management", {"userTable", "stat-total"}),
        ("admin_trash", set()),
        ("logs", {"logViewer", "clearFilters"}),
        ("audit_logs", {"loading-overlay"}),
        ("email_logs", {"loading-overlay"}),
        ("health_history", {"exportModal"}),
        ("sla_report", set()),
        ("stats", set()),
        ("changelog", set()),
        ("swagger_embed", set()),
        ("docs/api", set()),
        ("docs/security", set()),
    ],
)
def test_pages_keep_workflow_controls_and_a_single_page_heading(
    page: str, required_ids: set[str]
) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 8000),
            "path": "/" + page,
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "session": {"user_role": "admin", "user_name": "Administrator"},
            "router": Router(
                routes=[
                    Route("/health/history", lambda _request: None, name="health_history"),
                ]
            ),
        }
    )
    context = {
        "request": request,
        "configs": {},
        "timezones": ["UTC"],
        "user": SimpleNamespace(id=1, username="Operator", role="operator", is_2fa_enabled=False),
        "api_tokens": [],
        "camera_names": [],
        "camera_groups": [],
        "groups": [],
        "locations": [],
        "safety_options": [],
        "status_options": [],
        "search": "",
        "chart_data": [],
        "chart_labels": [],
        "selected_days": 7,
        "timing_logs": [],
        "selected_camera": "",
        "selected_group_id": None,
        "camera_exists": False,
        "images": [],
        "videos": [],
        "env_docs": {},
        "total_vars": 0,
        "error": "",
        "success": "",
        "templates": [],
        "variables": {},
        "nvrs": [],
        "users": [],
        "historical_data": [],
        "sla_data": [],
        "search_query": "",
        "current_sort": "hostname",
        "log_type": "main",
        "page": 1,
        "per_page": 20,
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 7),
        "sla_threshold": 99,
        "summary": {
            "total_cameras": 0,
            "average_uptime": 100,
            "compliance": {"pass": 0, "warning": 0, "fail": 0},
            "total_incidents": {"critical": 0, "major": 0, "minor": 0},
            "best_performer": None,
            "worst_performer": None,
        },
        "pagination": {
            "page": 1,
            "per_page": 20,
            "total": 0,
            "total_items": 0,
            "total_pages": 1,
            "has_prev": False,
            "has_next": False,
            "start_item": 0,
            "end_item": 0,
        },
    }
    response = templates.TemplateResponse(page + ".html", context)
    parsed = PageParser(response.body.decode())
    assert len([tag for tag, _ in parsed.elements if tag == "h1"]) == 1
    assert len([tag for tag, _ in parsed.elements if tag == "main"]) == 1
    assert len([tag for tag, _ in parsed.elements if tag == "head"]) == 1
    assert len([tag for tag, _ in parsed.elements if tag == "body"]) == 1
    ids = {attrs.get("id") for _, attrs in parsed.elements}
    assert required_ids <= ids
    if page == "cameras":
        assert any(
            tag == "form"
            and attrs.get("action") == "/cameras/upload_csv"
            and attrs.get("method") == "post"
            for tag, attrs in parsed.elements
        )
    if page == "change_password":
        passwords = [
            attrs
            for tag, attrs in parsed.elements
            if tag == "input" and attrs.get("type") == "password"
        ]
        assert len(passwords) == 3
        assert all("required" in attrs for attrs in passwords)


def test_shared_components_escape_content_and_reject_unknown_status_tones() -> None:
    template = templates.env.from_string(
        '{% from "macros/ui.html" import page_header, status_badge, empty_state, error_state %}'
        "{{ page_header(value, value) }}{{ status_badge(value, tone) }}"
        "{{ empty_state(value, value) }}{{ error_state(value, value) }}"
    )
    html = template.render(
        value='<script>alert("unsafe")</script>', tone='critical" onclick="unsafe'
    )
    parsed = PageParser(html)
    assert not any(tag == "script" or "onclick" in attrs for tag, attrs in parsed.elements)
    assert "&lt;script&gt;" in html
    assert any(attrs.get("class") == "ui-badge ui-badge-neutral" for _, attrs in parsed.elements)


def test_active_templates_compile() -> None:
    for path in Path("templates").rglob("*.html"):
        # Unrouted legacy view already contains malformed Jinja and duplicate scripts blocks.
        if path.name == "user_management2.html":
            continue
        templates.env.get_template(path.relative_to("templates").as_posix())


def test_camera_rows_escape_data_and_keep_action_arguments() -> None:
    name = 'Camera\'s "name" <img src=x onerror=unsafe()>'
    camera = SimpleNamespace(
        id="camera-id",
        hostname=name,
        ip="192.0.2.1",
        port=80,
        username="operator",
        latitude=None,
        longitude=None,
        asset_no="",
        location=name,
        groups=[SimpleNamespace(name=name)],
        safety_classification="critical",
        status="Maintenance",
    )
    rendered = templates.env.get_template("partials/camera_rows.html").render(cameras=[camera])
    parsed = PageParser(rendered)
    assert not any(tag == "img" for tag, _ in parsed.elements)
    buttons = [attrs for tag, attrs in parsed.elements if tag == "button"]
    assert len(buttons) == 4
    assert all(attrs.get("aria-label") and "ui-button-icon" in attrs["class"] for attrs in buttons)
    assert buttons[1]["onclick"].startswith('confirmDelete("camera-id", ')
    assert "Camera\\u0027s" in buttons[1]["onclick"]
    assert any("ui-badge-warning" in attrs.get("class", "") for _, attrs in parsed.elements)


def test_nvr_rows_escape_content_and_preserve_edit_delete_actions() -> None:
    name = 'NVR\'s "name" <img src=x onerror=unsafe()>'
    nvr = SimpleNamespace(
        id="nvr-id",
        hostname=name,
        ip="192.0.2.2",
        username="operator",
        location=name,
        group=SimpleNamespace(name=name),
        status="Active",
    )
    rendered = templates.env.get_template("partials/nvr_rows.html").render(nvrs=[nvr])
    parsed = PageParser(rendered)
    assert not any(tag == "img" for tag, _ in parsed.elements)
    buttons = [attrs for tag, attrs in parsed.elements if tag == "button"]
    assert len(buttons) == 2
    assert buttons[0]["onclick"] == 'showEditNVRModal("nvr-id")'
    assert buttons[1]["onclick"].startswith('confirmDelete("nvr-id", ')
    assert "ui-button-danger-quiet" in buttons[1]["class"]
    assert any("ui-badge-success" in attrs.get("class", "") for _, attrs in parsed.elements)


def test_health_history_pagination_keeps_filters_and_numbered_window() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 8000),
            "path": "/health/history",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "session": {},
            "router": Router(
                routes=[
                    Route("/health/history", lambda _request: None, name="health_history"),
                ]
            ),
        }
    )
    response = templates.TemplateResponse(
        "health_history.html",
        {
            "request": request,
            "historical_data": [],
            "camera_groups": [],
            "search_query": "Camera & Gate",
            "current_sort": "hostname",
            "pagination": {
                "page": 5,
                "total_pages": 10,
                "total": 200,
                "start_item": 81,
                "end_item": 100,
                "has_prev": True,
                "has_next": True,
                "prev_num": 4,
                "next_num": 6,
            },
        },
    )
    html = response.body.decode()
    parsed = PageParser(html)
    links = [
        attrs for tag, attrs in parsed.elements if tag == "a" and "?page=" in attrs.get("href", "")
    ]
    assert len(links) == 9
    assert all("q=Camera%20%26%20Gate" in link["href"] for link in links)
    assert len([link for link in links if link.get("aria-current") == "page"]) == 1
    assert "Page 5 of 10" in html
    assert "Showing 81-100 of 200 results" in html


@pytest.mark.parametrize(
    ("page", "action", "token_name"),
    [
        ("login", "/login", None),
        ("login_2fa", "/login/otp", "token_2fa"),
        ("force_mfa_setup", "/mfa/setup", "token_mfa"),
        ("setup", None, None),
    ],
)
def test_authentication_forms_keep_their_post_endpoints_and_tokens(
    page: str, action: str | None, token_name: str | None
) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/login",
            "scheme": "http",
            "server": ("localhost", 8000),
            "query_string": b"",
            "headers": [],
            "router": Router(routes=[Route("/login", lambda _request: None, name="login_form")]),
        }
    )
    html = templates.env.get_template(page + ".html").render(
        request=request,
        error="",
        token_2fa="verification-token",
        token_mfa="verification-token",
        qr_code="",
    )
    parsed = PageParser(html)
    forms = [attrs for tag, attrs in parsed.elements if tag == "form"]
    assert any(form.get("method") == "post" and form.get("action") == action for form in forms)
    if token_name:
        assert any(
            tag == "input"
            and attrs.get("name") == token_name
            and attrs.get("value") == "verification-token"
            for tag, attrs in parsed.elements
        )
