"""Rendering and safety checks for the shared UI and representative workflows."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.routing import Route, Router

from app.utils.template_helper import templates


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
        ("cameras", {
            "searchInput", "camera-table", "cameraForm", "csvFileInput",
            "locationFilter", "safetyFilter", "statusFilter",
        }),
        ("user_profile", {"tab-password", "tab-api_keys", "changePasswordForm"}),
        ("change_password", {"current_password", "new_password", "confirm_password"}),
        ("config", {"configForm", "configTabBar", "config-tab-general", "saveButton"}),
        ("wa_bot_builder", {"commandActions", "commandsTab", "messagesPanel", "emptyAddCommand"}),
        ("snapshot_gallery", {"searchInput", "cameraSelect", "clearFilterBtn"}),
        ("video_gallery", {"searchInput", "cameraSelect", "clearFilterBtn"}),
        ("docs/environment", set()),
    ],
)
def test_pages_keep_workflow_controls_and_a_single_page_heading(
    page: str, required_ids: set[str]
) -> None:
    request = Request(
        {
            "type": "http", "method": "GET", "scheme": "http",
            "server": ("localhost", 8000), "path": "/" + page,
            "root_path": "", "query_string": b"", "headers": [],
            "session": {"user_role": "admin", "user_name": "Administrator"},
        }
    )
    context = {
        "request": request, "configs": {}, "timezones": ["UTC"],
        "user": SimpleNamespace(id=1, username="Operator", role="operator", is_2fa_enabled=False),
        "api_tokens": [], "camera_names": [], "camera_groups": [], "groups": [],
        "locations": [], "safety_options": [], "status_options": [], "search": "",
        "chart_data": [], "chart_labels": [], "selected_days": 7, "timing_logs": [],
        "selected_camera": "", "selected_group_id": None, "camera_exists": False,
        "images": [], "videos": [], "env_docs": {}, "total_vars": 0,
        "error": "", "success": "",
    }
    response = templates.TemplateResponse(page + ".html", context)
    parsed = PageParser(response.body.decode())
    assert len([tag for tag, _ in parsed.elements if tag == "h1"]) == 1
    assert len([tag for tag, _ in parsed.elements if tag == "main"]) == 1
    ids = {attrs.get("id") for _, attrs in parsed.elements}
    assert required_ids <= ids
    if page == "cameras":
        assert any(
            tag == "form" and attrs.get("action") == "/cameras/upload_csv"
            and attrs.get("method") == "post"
            for tag, attrs in parsed.elements
        )
    if page == "change_password":
        passwords = [
            attrs for tag, attrs in parsed.elements
            if tag == "input" and attrs.get("type") == "password"
        ]
        assert len(passwords) == 3
        assert all("required" in attrs for attrs in passwords)


def test_shared_components_escape_content_and_reject_unknown_status_tones() -> None:
    template = templates.env.from_string(
        '{% from "macros/ui.html" import page_header, status_badge, empty_state, error_state %}'
        '{{ page_header(value, value) }}{{ status_badge(value, tone) }}'
        '{{ empty_state(value, value) }}{{ error_state(value, value) }}'
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


@pytest.mark.parametrize(
    ("page", "action", "token_name"),
    [("login", "/login", None), ("login_2fa", "/login/otp", "token_2fa"),
     ("force_mfa_setup", "/mfa/setup", "token_mfa"), ("setup", None, None)],
)
def test_authentication_forms_keep_their_post_endpoints_and_tokens(
    page: str, action: str | None, token_name: str | None
) -> None:
    request = Request({
        "type": "http", "method": "GET", "path": "/login", "scheme": "http",
        "server": ("localhost", 8000), "query_string": b"", "headers": [],
        "router": Router(routes=[Route("/login", lambda request: None, name="login_form")]),
    })
    html = templates.env.get_template(page + ".html").render(
        request=request, error="", token_2fa="verification-token",
        token_mfa="verification-token", qr_code="",
    )
    parsed = PageParser(html)
    forms = [attrs for tag, attrs in parsed.elements if tag == "form"]
    assert any(form.get("method") == "post" and form.get("action") == action for form in forms)
    if token_name:
        assert any(
            tag == "input" and attrs.get("name") == token_name
            and attrs.get("value") == "verification-token"
            for tag, attrs in parsed.elements
        )
