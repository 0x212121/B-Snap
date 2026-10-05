"""Regression checks for destination access and active navigation states."""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from starlette.templating import _TemplateResponse
from starlette.requests import Request

from app.utils.template_helper import templates


class NavigationParser(HTMLParser):
    def __init__(self, html: bytes) -> None:
        super().__init__()
        self.links: dict[str, list[dict[str, str | None]]] = {}
        self.current_nav: str | None = None
        self.ids: set[str] = set()
        self.controls: set[str] = set()
        self.feed(html.decode("utf-8"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if attributes.get("aria-controls"):
            self.controls.add(attributes["aria-controls"])
        if tag == "nav":
            self.current_nav = attributes.get("aria-label")
            self.links[self.current_nav] = []
        if tag == "a" and self.current_nav:
            self.links[self.current_nav].append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            self.current_nav = None


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Navigation rendering does not use the application's database fixture."""


def render_navigation(role: str, path: str = "/maps") -> _TemplateResponse:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("localhost", 8000),
            "path": path,
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "session": {"user_role": role, "user_name": "Test user"},
        }
    )
    return templates.TemplateResponse("base.html", {"request": request})


@pytest.mark.parametrize("role", ["admin", "operator", "viewer"])
def test_destination_permissions_are_preserved(role: str) -> None:
    response = render_navigation(role)
    expected = {"/maps"}
    if role in {"admin", "operator"}:
        expected |= {"/snap_gallery", "/videos"}
    if role == "admin":
        expected |= {
            "/analytics", "/cameras", "/nvrs", "/admin/record-checks", "/health",
            "/admin/users", "/admin/camera-groups", "/admin/whitelist", "/admin/wa-bot",
            "/admin/recipients", "/admin/email-templates", "/admin/config", "/admin/jobs",
            "/admin/powerbi-record-checks", "/admin/trash", "/logs", "/audit-logs",
            "/email-logs", "/developer/docs", "/docs/environment", "/documentation/index.html",
        }
    parsed = NavigationParser(response.body)
    for label in ("Main navigation", "Mobile navigation"):
        assert {link["href"] for link in parsed.links[label]} == expected
    assert parsed.controls <= parsed.ids


@pytest.mark.parametrize(
    ("path", "active_href"),
    [("/admin/wa-bot", "/admin/wa-bot"), ("/cameras/12", "/cameras"),
     ("/cameras-extra", None), ("/analytics", "/analytics")],
)
def test_active_destination_is_a_link_with_a_route_boundary(
    path: str, active_href: str | None
) -> None:
    response = render_navigation("admin", path)
    parsed = NavigationParser(response.body)
    for label in ("Main navigation", "Mobile navigation"):
        active = [link for link in parsed.links[label] if link.get("aria-current") == "page"]
        assert [link.get("href") for link in active] == ([active_href] if active_href else [])
