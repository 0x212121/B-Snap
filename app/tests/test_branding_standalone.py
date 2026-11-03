"""Branding validation, rendering and persistence without a real database."""

import asyncio
import unittest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.branding import DEFAULT_BRANDING, get_branding, read_branding, validate_branding
from app.routes.config import save_branding


class BrandingTests(unittest.TestCase):
    def test_defaults_and_invalid_configuration(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        assert read_branding(db) == DEFAULT_BRANDING
        db.query.return_value.filter.return_value.all.return_value = [
            SimpleNamespace(key="branding_logo", value="https://example.com/logo.png")
        ]
        assert read_branding(db) == DEFAULT_BRANDING
        db.query.return_value.filter.return_value.all.return_value.append(
            SimpleNamespace(key="branding_name", value="Acme")
        )
        branding = read_branding(db)
        assert branding["name"] == "Acme"
        assert branding["logo"] == DEFAULT_BRANDING["logo"]
        with patch("app.core.branding.SessionLocal", side_effect=RuntimeError("offline")):
            assert get_branding() == DEFAULT_BRANDING

    def test_validation_rejects_arbitrary_urls_and_controls(self):
        invalid = [
            ("", "", DEFAULT_BRANDING["logo"]),
            ("Name\nOther", "", DEFAULT_BRANDING["logo"]),
            ("Name", "", "https://example.com/x.png"),
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):  # noqa: PT027
                validate_branding(*values)

    def test_template_escapes_brand_name_in_page_title(self):
        env = Environment(
            loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"])
        )
        template = env.from_string(
            '{% extends "base.html" %}{% block title %}B-SNAP - Cameras{% endblock %}'
        )
        request = SimpleNamespace(session={}, url=SimpleNamespace(path="/"))
        rendered = template.render(
            branding={"name": "<Acme>", "logo": DEFAULT_BRANDING["logo"], "tagline": ""},
            request=request,
            nav_items=[],
            timezone="UTC",
        )
        assert "<title>&lt;Acme&gt; - Cameras</title>" in rendered
        assert 'alt="&lt;Acme&gt;"' in rendered

    def test_login_renders_identity_safely(self):
        env = Environment(
            loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"])
        )
        request = SimpleNamespace(query_params={})
        rendered = env.get_template("login.html").render(
            branding={"name": "<Acme>", "logo": DEFAULT_BRANDING["logo"], "tagline": "A & B"},
            request=request,
        )
        assert "<title>&lt;Acme&gt; - Sign In</title>" in rendered
        assert "A &amp; B" in rendered

    def test_custom_logo_is_used_as_favicon(self):
        env = Environment(
            loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"])
        )
        rendered = env.get_template("login.html").render(
            branding={"name": "Acme", "logo": "/static/branding/logo.png", "tagline": ""},
            request=SimpleNamespace(query_params={}),
        )
        assert '<link rel="icon" href="/static/branding/logo.png">' in rendered

    def test_palette_is_validated_and_applied_to_login(self):
        branding = validate_branding("Acme", "", DEFAULT_BRANDING["logo"], "teal")
        env = Environment(
            loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"])
        )
        rendered = env.get_template("login.html").render(
            branding=branding, request=SimpleNamespace(query_params={})
        )
        assert "--ui-primary: #0f766e" in rendered
        with self.assertRaises(ValueError):  # noqa: PT027
            validate_branding("Acme", "", DEFAULT_BRANDING["logo"], "red; color: red")

    def test_save_only_branding_fields(self):
        db = MagicMock()
        db.get.return_value = None
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        with patch(
            "app.routes.config.log_audit", side_effect=lambda **kwargs: kwargs["db"].commit()
        ) as audit:
            response = asyncio.run(
                save_branding(
                    request=request,
                    db=db,
                    current_admin=SimpleNamespace(username="admin"),
                    name="New name",
                    tagline="Welcome",
                    logo=DEFAULT_BRANDING["logo"],
                )
            )
        assert response.status_code == 200
        assert {call.args[0].key for call in db.add.call_args_list} == {
            "branding_name",
            "branding_tagline",
            "branding_logo",
            "branding_palette",
        }
        db.commit.assert_called_once()
        audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
