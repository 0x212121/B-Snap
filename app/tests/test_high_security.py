"""High-priority security boundaries using real ASGI requests and isolated tables."""

from __future__ import annotations

import hashlib
import hmac
import json

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app import main
from app.core.security import load_security_settings
from app.db.database import get_db
from app.middleware import auth_and_setup
from app.middleware.csrf import CSRFMiddleware
from app.models.auth_rate_limit import AuthRateLimit
from app.routes import auth, wa_webhook
from app.utils import auth_rate_limit

TEST_WEBHOOK_SECRET = "test-only-webhook-signing-key-32-bytes"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override schema-wide SQLite initialization; these tests own their tables."""


def signature(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_main_stack_rejects_csrf_and_unsigned_webhooks_before_database_work(monkeypatch):
    database = Mock(side_effect=AssertionError("Unexpected database work"))
    monkeypatch.setattr(auth_and_setup, "SessionLocal", database)
    monkeypatch.setenv("GOWA_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    # No context manager: do not run lifespan, migrations, or background listeners.
    client = TestClient(main.app)
    assert client.post("/login", data={"username": "test", "password": "test"}).status_code == 403
    response = client.post("/webhook/gowa", content=b"{}")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"
    database.assert_not_called()
    client.close()


@pytest.fixture
def webhook_client(monkeypatch):
    monkeypatch.setenv("GOWA_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    app = FastAPI()
    app.include_router(wa_webhook.router)
    app.add_middleware(CSRFMiddleware)
    db = Mock()
    bot = Mock()
    bot.return_value.parse_command.return_value = ("snap", "denied")
    monkeypatch.setattr(wa_webhook, "WABotHandler", bot)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db, bot


@pytest.mark.parametrize("header", [None, "", "sha1=abc", "sha256=bad", "sha256=" + "0" * 64])
def test_unsigned_and_invalid_webhooks_never_query_or_execute(webhook_client, header):
    client, db, bot = webhook_client
    headers = {"X-Hub-Signature-256": header} if header is not None else {}
    response = client.post(
        "/webhook/gowa", content=b'{"from":"628123456789","body":"/snap"}', headers=headers
    )
    assert response.status_code == 401
    db.query.assert_not_called()
    db.add.assert_not_called()
    bot.assert_not_called()


def test_signature_covers_raw_bytes_before_json_parsing(webhook_client):
    client, db, bot = webhook_client
    raw = b'{ "event" : "ignored", "text": "space-preserved" }'
    assert (
        client.post(
            "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw)}
        ).status_code
        == 200
    )
    altered = json.dumps(json.loads(raw), separators=(",", ":")).encode()
    assert (
        client.post(
            "/webhook/gowa", content=altered, headers={"X-Hub-Signature-256": signature(raw)}
        ).status_code
        == 401
    )
    db.query.assert_not_called()
    bot.assert_not_called()


def test_valid_webhook_still_enforces_whitelist_before_bot(webhook_client, monkeypatch):
    client, db, _bot = webhook_client
    payload = {"from": "628123456789", "body": "/snap denied", "id": "unique-message"}
    raw = json.dumps(payload).encode()
    allowed = Mock(return_value=None)
    monkeypatch.setattr(wa_webhook, "_whitelisted_sender", allowed)
    response = client.post(
        "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw)}
    )
    assert response.status_code == 200
    allowed.assert_called_once_with(db, payload["from"])
    db.add.assert_not_called()


def test_missing_secret_fails_closed_and_is_not_reflected(webhook_client, monkeypatch):
    client, db, bot = webhook_client
    monkeypatch.delenv("GOWA_WEBHOOK_SECRET")
    response = client.post("/webhook/gowa", content=b"{}")
    assert response.status_code == 503
    assert TEST_WEBHOOK_SECRET not in response.text
    db.query.assert_not_called()
    bot.assert_not_called()


def test_gateway_variable_alone_does_not_configure_bsnap(webhook_client, monkeypatch):
    client, db, bot = webhook_client
    monkeypatch.delenv("GOWA_WEBHOOK_SECRET")
    monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    raw = b'{"event":"ignored"}'
    response = client.post(
        "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw)}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Webhook authentication is not configured"
    assert TEST_WEBHOOK_SECRET not in response.text
    db.query.assert_not_called()
    bot.assert_not_called()


def test_bsnap_uses_its_own_secret_when_gateway_variable_is_present(webhook_client, monkeypatch):
    client, db, bot = webhook_client
    gateway_secret = "different-gateway-test-signing-key-32-bytes"
    monkeypatch.setenv("WHATSAPP_WEBHOOK_SECRET", gateway_secret)
    raw = b'{"event":"ignored"}'
    for secret, expected_status in [(TEST_WEBHOOK_SECRET, 200), (gateway_secret, 401)]:
        response = client.post(
            "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw, secret)}
        )
        assert response.status_code == expected_status
        assert secret not in response.text
    db.query.assert_not_called()
    bot.assert_not_called()


def test_signed_duplicate_provider_message_is_ignored_before_bot_action(
    webhook_client, monkeypatch
):
    client, db, bot = webhook_client
    monkeypatch.setattr(
        wa_webhook, "_whitelisted_sender", Mock(return_value=SimpleNamespace(role="user"))
    )
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(id=1)
    raw = b'{"from":"628123456789","body":"/snap gate","id":"already-seen"}'
    response = client.post(
        "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw)}
    )
    assert response.status_code == 200
    bot.return_value.handle.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"not-json", 400), (b"x" * (1024 * 1024 + 1), 413)],
    ids=["invalid-json", "oversized"],
)
def test_signed_webhooks_reject_invalid_json_and_oversize_body(webhook_client, raw, expected):
    client, db, bot = webhook_client
    response = client.post(
        "/webhook/gowa", content=raw, headers={"X-Hub-Signature-256": signature(raw)}
    )
    assert response.status_code == expected
    db.query.assert_not_called()
    bot.assert_not_called()


def test_duplicate_signature_headers_are_rejected(webhook_client):
    client, db, _bot = webhook_client
    raw = b"{}"
    response = client.post(
        "/webhook/gowa", content=raw, headers=[("X-Hub-Signature-256", signature(raw))] * 2
    )
    assert response.status_code == 401
    db.query.assert_not_called()


@pytest.fixture
def csrf_client():
    app = FastAPI()
    touched = Mock()
    app.add_middleware(CSRFMiddleware)

    @app.api_route("/mutate", methods=["POST", "PUT", "DELETE"])
    async def mutation():
        touched()
        return {"ok": True}

    with TestClient(app) as client:
        yield client, touched


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Origin": "http://[broken"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://testserver", "Sec-Fetch-Site": "same-site"},
        {"Referer": "https://evil.example/form"},
    ],
)
def test_browser_mutations_fail_without_same_origin_evidence(csrf_client, headers, method):
    client, touched = csrf_client
    response = client.request(method, "/mutate", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_REJECTED"
    touched.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://testserver"},
        {"Origin": "http://testserver:80"},
        {"Referer": "http://testserver/form"},
        {"Sec-Fetch-Site": "same-origin"},
    ],
)
def test_native_form_and_ajax_origin_evidence_is_supported(csrf_client, headers):
    client, touched = csrf_client
    assert client.post("/mutate", headers=headers, data={"field": "value"}).status_code == 200
    touched.assert_called_once()


def test_bearer_exemption_cannot_mask_browser_cookies(csrf_client):
    client, touched = csrf_client
    assert client.post("/mutate", headers={"Authorization": "Bearer api"}).status_code == 200
    client.cookies.set("session_token", "browser")
    assert client.post("/mutate", headers={"Authorization": "Bearer api"}).status_code == 403
    touched.assert_called_once()


def test_duplicate_origin_and_referer_headers_fail_closed(csrf_client):
    client, touched = csrf_client
    for key in ("Origin", "Referer"):
        response = client.post(
            "/mutate", headers=[(key, "http://testserver"), (key, "https://evil.example")]
        )
        assert response.status_code == 403
    touched.assert_not_called()


def test_public_origin_supports_proxy_host_rewriting():
    app = FastAPI()
    app.add_middleware(CSRFMiddleware, public_origin="https://bsnap.example")

    @app.post("/form")
    def form():
        return {"ok": True}

    with TestClient(app, base_url="http://internal:8080") as client:
        assert client.post("/form", headers={"Origin": "https://bsnap.example"}).status_code == 200
        assert client.post("/form", headers={"Origin": "http://internal:8080"}).status_code == 403


def test_get_logout_preserves_session_and_post_requires_csrf(monkeypatch):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="logout-test")
    app.add_middleware(CSRFMiddleware)
    app.include_router(auth.router)
    db = Mock()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(
        auth.templates, "TemplateResponse", lambda *_args: JSONResponse({"confirm": True})
    )

    @app.get("/session")
    def session(request: Request):
        request.session["user_id"] = 1
        return {"user": request.session["user_id"]}

    @app.get("/identity")
    def identity(request: Request):
        return {"user": request.session.get("user_id")}

    with TestClient(app) as client:
        client.get("/session")
        assert client.get("/logout").status_code == 200
        assert client.get("/identity").json()["user"] == 1
        assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
        assert (
            client.post(
                "/logout", headers={"Origin": "http://testserver"}, follow_redirects=False
            ).status_code
            == 303
        )
        assert client.get("/identity").json()["user"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"SECRET_KEY": "dev-secret"},
        {"SECRET_KEY": "your-very-secret-key-change-this-in-production"},
        {"SECRET_KEY": "x" * 32, "COOKIE_SECURE": "false"},
        {"SECRET_KEY": "x" * 32, "BEHIND_HTTPS_PROXY": "true"},
        {"SECRET_KEY": "x" * 32, "TRUSTED_PROXY_HOSTS": "*"},
        {"SECRET_KEY": "x" * 32, "TRUSTED_PROXY_HOSTS": "0.0.0.0/0"},
        {"SECRET_KEY": "x" * 32, "TRUSTED_PROXY_HOSTS": "::/0"},
        {"SECRET_KEY": "x" * 32, "COOKIE_SAMESITE": "invalid"},
        {"SECRET_KEY": "x" * 32, "PUBLIC_ORIGIN": "http://bsnap.example"},
    ],
)
def test_production_rejects_missing_or_unsafe_settings_without_secret_leak(overrides):
    with pytest.raises(
        ValueError, match=r"SECRET_KEY|cookies|TRUSTED_PROXY_HOSTS|COOKIE_SAMESITE|PUBLIC_ORIGIN"
    ) as error:
        load_security_settings({"ENVIRONMENT": "production", **overrides})
    if overrides.get("SECRET_KEY"):
        assert overrides["SECRET_KEY"] not in str(error.value)


def test_production_https_proxy_settings_are_explicit_and_shared():
    settings = load_security_settings(
        {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "x" * 40,
            "BEHIND_HTTPS_PROXY": "true",
            "TRUSTED_PROXY_HOSTS": "127.0.0.1,10.10.0.0/24",
            "PUBLIC_ORIGIN": "https://bsnap.example",
        }
    )
    assert settings.cookie_secure
    assert settings.cookie_samesite == "lax"
    assert settings.trusted_proxies == ("127.0.0.1", "10.10.0.0/24")
    assert settings.secret_key not in repr(settings)
    assert load_security_settings({"ENVIRONMENT": "testing"}).cookie_secure is False


@pytest.fixture
def rate_db():
    engine = create_engine("sqlite://")
    AuthRateLimit.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def rate_request(peer="192.0.2.1"):
    return Request({"type": "http", "headers": [], "client": (peer, 1)})


def test_identity_limit_persists_across_sessions_and_returns_retry_after(rate_db):
    for _ in range(auth_rate_limit.IDENTITY_ATTEMPTS_PER_FIVE_MINUTES):
        auth_rate_limit.enforce_auth_rate_limit(rate_db, rate_request(), "password", "operator")
    with Session(rate_db.get_bind()) as other, pytest.raises(HTTPException) as error:
        auth_rate_limit.enforce_auth_rate_limit(
            other, rate_request("192.0.2.2"), "password", "OPERATOR"
        )
    assert error.value.status_code == 429
    assert 1 <= int(error.value.headers["Retry-After"]) <= 300
    assert all(len(row.bucket) == 64 for row in rate_db.scalars(select(AuthRateLimit)).all())


def test_expired_windows_reset_and_password_otp_scopes_are_separate(rate_db):
    auth_rate_limit.enforce_auth_rate_limit(rate_db, rate_request(), "password", "operator")
    for row in rate_db.scalars(select(AuthRateLimit)):
        row.attempts = 100
        row.window_end = datetime.now(UTC) - timedelta(seconds=1)
    rate_db.commit()
    auth_rate_limit.enforce_auth_rate_limit(rate_db, rate_request(), "password", "operator")
    auth_rate_limit.enforce_auth_rate_limit(rate_db, rate_request(), "otp", "operator")
    assert all(row.attempts == 1 for row in rate_db.scalars(select(AuthRateLimit)))


def test_auth_failure_to_update_shared_counter_fails_closed():
    db = Mock()
    db.execute.side_effect = RuntimeError("private-db-detail")
    with pytest.raises(HTTPException) as error:
        auth_rate_limit.enforce_auth_rate_limit(db, rate_request(), "password", "operator")
    assert error.value.status_code == 503
    assert "private-db-detail" not in error.value.detail
    db.rollback.assert_called_once()


@pytest.fixture
def login_api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    AuthRateLimit.__table__.create(engine)
    with Session(engine) as actual_db:
        db = Mock()
        for method in ("get_bind", "execute", "commit", "rollback"):
            setattr(db, method, getattr(actual_db, method))
        db.query.return_value.filter.return_value.first.return_value = None
        app = FastAPI(exception_handlers=main.app.exception_handlers.copy())
        app.add_middleware(SessionMiddleware, secret_key="auth-limit-test")
        app.add_middleware(CSRFMiddleware)
        app.include_router(auth.router)
        app.dependency_overrides[get_db] = lambda: db
        renderer = Mock(
            return_value=JSONResponse({"error": "invalid credentials"}, status_code=401)
        )
        monkeypatch.setattr(auth.templates, "TemplateResponse", renderer)
        with TestClient(
            app, headers={"Origin": "http://testserver", "Accept": "application/json"}
        ) as client:
            yield client, db
    engine.dispose()


def test_password_limit_is_enforced_before_user_lookup(login_api):
    client, db = login_api
    for _ in range(10):
        assert (
            client.post("/login", data={"username": "operator", "password": "invalid"}).status_code
            == 401
        )
    response = client.post("/login", data={"username": "operator", "password": "invalid"})
    assert response.status_code == 429
    assert response.headers["retry-after"]
    assert db.query.call_count == 10


def test_otp_and_enrollment_share_a_limit_before_totp_verification(login_api, monkeypatch):
    client, db = login_api
    user = SimpleNamespace(
        id=1, username="operator", is_2fa_enabled=True, otp_secret="JBSWY3DPEHPK3PXP"
    )
    db.query.return_value.options.return_value.filter.return_value.first.return_value = user
    totp = Mock()
    totp.return_value.verify.return_value = False
    monkeypatch.setattr(auth.pyotp, "TOTP", totp)
    token = auth.create_2fa_token(1)
    for _ in range(10):
        assert (
            client.post("/login/otp", data={"token_2fa": token, "otp": "invalid"}).status_code
            == 401
        )
    assert client.post("/mfa/setup", data={"token_mfa": token, "otp": "invalid"}).status_code == 429
    assert totp.return_value.verify.call_count == 10
