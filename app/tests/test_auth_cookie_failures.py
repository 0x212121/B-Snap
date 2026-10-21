"""Regression tests for temporary failures without production DB/bootstrap.

Run with python -m unittest app.tests.test_auth_cookie_failures -v. Production
modules are executed with application imports replaced by mocks. HTTP tests use
real session middleware; token transaction tests use only compatible SQLite models.
This avoids the full-schema autouse pytest fixture and PostgreSQL JSONB models.
"""

# ruff: noqa: PT009, PT027

from __future__ import annotations

import ast
import asyncio
import unittest

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, create_engine
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy.pool import QueuePool
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, namespace: dict) -> dict:
    """Execute actual definitions while replacing only application imports."""

    class RemoveAppImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
            if node.module and node.module.startswith("app."):
                return None
            return node

    tree = RemoveAppImports().visit(ast.parse(path.read_text(encoding="utf-8")))
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), namespace)
    return namespace


class AuthPoolBurstTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_media_requests_do_not_hold_auth_connections(self) -> None:
        """Model a proxy request burst with a real, isolated, two-connection pool."""
        engine = create_engine(
            "sqlite://",
            poolclass=QueuePool,
            pool_size=2,
            max_overflow=0,
            pool_timeout=0.01,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(engine.dispose)
        user = SimpleNamespace(
            id=1,
            role="operator",
            username="test",
            group_id=None,
            web_tokens=[
                {
                    "token": "test-session",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
                }
            ],
        )

        def session_factory() -> Mock:
            db = Mock()
            lease = None

            def query(*_args: object) -> Mock:
                nonlocal lease
                if lease is None:
                    lease = engine.connect()
                return db.query.return_value

            def close() -> None:
                nonlocal lease
                if lease is not None:
                    lease.close()
                    lease = None

            db.query.side_effect = query
            db.query.return_value.first.return_value = user
            db.query.return_value.filter.return_value.first.return_value = user
            db.close.side_effect = close
            db.rollback.side_effect = close
            return db

        namespace = load_module(ROOT / "utils/auth_token.py", {})
        namespace.update(
            SessionLocal=session_factory,
            User=Mock(),
            Configuration=Mock(),
            get_online_tracker=Mock(return_value=Mock()),
            get_cookie_settings=lambda: {"key": "remember_me"},
        )
        load_module(ROOT / "middleware/auth_and_setup.py", namespace)
        namespace["logger"] = Mock()
        middleware = namespace["AuthAndSetupMiddleware"](FastAPI())

        async def endpoint(_request: Request) -> Response:
            await asyncio.sleep(0)  # Other requests authenticate while this one is in flight.
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return Response(b"image", media_type="image/jpeg")

        def request() -> Request:
            return Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/snapshots/secure/test",
                    "scheme": "https",
                    "query_string": b"thumb=true&preview=true",
                    "headers": [(b"cookie", b"session_token=test-session")],
                    "session": {"user_id": 1},
                    "client": ("127.0.0.1", 1234),
                    "server": ("test", 443),
                }
            )

        responses = await asyncio.gather(
            *(middleware.dispatch(request(), endpoint) for _ in range(8))
        )
        self.assertEqual([response.status_code for response in responses], [200] * 8)
        self.assertEqual(engine.pool.checkedout(), 0)


class AuthCookieFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = Mock()
        self.user = SimpleNamespace(
            id=1,
            role="viewer",
            username="test-user",
            group_id=None,
            web_tokens=[
                {
                    "token": "test-session",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=12)).isoformat(),
                }
            ],
        )
        self.db.query.return_value.first.return_value = self.user
        self.db.query.return_value.filter.return_value.first.return_value = self.user
        self.db.query.return_value.get.return_value = self.user
        self.db.execute.return_value.first.return_value = None
        self.ns = load_module(ROOT / "utils/auth_token.py", {})
        self.ns.update(
            SessionLocal=Mock(return_value=self.db),
            User=Mock(),
            Configuration=Mock(),
            get_online_tracker=Mock(return_value=Mock()),
            get_cookie_settings=lambda: {
                "key": "remember_me",
                "max_age": 31536000,
                "secure": False,
                "samesite": "lax",
            },
            validate_remember_token=Mock(return_value=self.user),
            revoke_token=Mock(return_value=True),
            create_remember_token=Mock(return_value="new-remember"),
            SESSION_MAX_AGE_SECONDS=86400,
            COOKIE_SAMESITE="lax",
            COOKIE_SECURE=False,
        )
        load_module(ROOT / "middleware/auth_and_setup.py", self.ns)
        # The test must not emit exceptions containing mocked credentials.
        self.ns["logger"] = Mock()
        app = FastAPI()
        app.add_middleware(self.ns["AuthAndSetupMiddleware"])
        app.add_middleware(SessionMiddleware, secret_key="isolated-cookie-regression")
        self.calls = 0
        self.failure = None
        self.mutate_session = False

        @app.get("/login/seed")
        async def seed(request: Request, kind: str = "session") -> Response:
            response = Response("seed")
            if kind == "session":
                request.session.update(user_id=1, user_role="viewer", custom="keep")
            if kind in {"session", "token"}:
                response.set_cookie("session_token", "test-session")
            if kind == "remember":
                response.set_cookie("remember_me", "test-remember")
            return response

        @app.get("/protected")
        @app.post("/login/failure")
        async def endpoint(request: Request) -> Response:
            self.calls += 1
            if self.mutate_session:
                request.session.clear()
            if self.failure is not None:
                raise self.failure
            return JSONResponse(dict(request.session))

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def seed(self, kind: str = "session") -> None:
        self.client.get("/login/seed", params={"kind": kind})
        self.db.reset_mock()

    def assert_temporary_error(self, path: str = "/protected", method: str = "GET") -> None:
        previous_calls = self.calls
        response = self.client.request(method, path, follow_redirects=False)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(), {"detail": "Service temporarily unavailable. Please try again."}
        )
        self.assertFalse(any("Max-Age=0" in h for h in response.headers.get_list("set-cookie")))
        self.db.rollback.assert_called_once()
        # Close before downstream work, then again after any post-response DB work.
        self.assertEqual(self.db.close.call_count, 2 if self.calls > previous_calls else 1)

    def test_valid_session_still_reaches_endpoint(self) -> None:
        self.seed()
        self.assertEqual(self.client.get("/protected").status_code, 200)
        self.assertEqual(self.calls, 1)

    def test_endpoint_errors_preserve_session_and_allow_retry(self) -> None:
        for error in (TimeoutError("slow upstream"), ValueError("endpoint"), TypeError("endpoint")):
            with self.subTest(error=type(error).__name__):
                self.seed()
                self.failure = error
                self.mutate_session = True
                self.assert_temporary_error()
                self.assertEqual(self.client.cookies["session_token"], "test-session")
                self.failure = None
                self.mutate_session = False
                self.assertEqual(self.client.get("/protected").json()["custom"], "keep")

    def test_database_setup_check_failure_does_not_force_login(self) -> None:
        self.seed()
        self.db.query.side_effect = ConnectionError("unavailable")
        self.assert_temporary_error()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.client.cookies["session_token"], "test-session")

    def test_session_lookup_failure_preserves_credentials(self) -> None:
        self.seed()
        self.db.query.return_value.filter.return_value.first.side_effect = TimeoutError()
        self.assert_temporary_error()
        self.assertEqual(self.calls, 0)

    def test_session_factory_failure_returns_temporary_error(self) -> None:
        self.seed()
        self.ns["SessionLocal"].side_effect = ConnectionError()
        response = self.client.get("/protected", follow_redirects=False)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(any("Max-Age=0" in h for h in response.headers.get_list("set-cookie")))
        self.db.close.assert_not_called()

    def test_cookie_fallback_database_failure_does_not_expire_token(self) -> None:
        self.seed("token")
        self.db.execute.side_effect = TimeoutError()
        self.assert_temporary_error()
        self.assertEqual(self.client.cookies["session_token"], "test-session")
        self.assertEqual(self.calls, 0)

    def test_cookie_fallback_endpoint_failure_is_not_authentication_failure(self) -> None:
        self.seed("token")
        self.db.execute.return_value.first.return_value = SimpleNamespace(id=1)
        self.failure = TimeoutError()
        self.assert_temporary_error()
        self.assertEqual(self.client.cookies["session_token"], "test-session")
        self.assertEqual(self.calls, 1)

    def test_failed_restore_after_tentative_session_clear_preserves_signed_session(self) -> None:
        self.seed()
        original_tokens = self.user.web_tokens
        self.user.web_tokens = []
        self.db.execute.side_effect = ConnectionError()
        self.assert_temporary_error()
        self.user.web_tokens = original_tokens
        self.db.execute.side_effect = None
        self.assertEqual(self.client.get("/protected").json()["custom"], "keep")

    def test_remember_validation_failure_preserves_cookie(self) -> None:
        self.seed("remember")
        self.ns["validate_remember_token"].side_effect = TimeoutError()
        self.assert_temporary_error()
        self.assertEqual(self.client.cookies["remember_me"], "test-remember")
        self.ns["revoke_token"].assert_not_called()

    def test_remember_endpoint_failure_does_not_revoke_old_token(self) -> None:
        self.seed("remember")
        self.failure = TimeoutError()
        self.assert_temporary_error()
        self.ns["revoke_token"].assert_not_called()
        self.db.commit.assert_called_once()
        self.assertEqual(self.client.cookies["remember_me"], "test-remember")

    def test_remember_rotation_failure_rolls_back_without_expiring_cookie(self) -> None:
        self.seed("remember")
        self.ns["create_remember_token"].side_effect = ConnectionError()
        self.assert_temporary_error()
        self.ns["revoke_token"].assert_called_once_with(self.db, "test-remember", commit=False)
        self.assertFalse(self.ns["create_remember_token"].call_args.kwargs["commit"])
        # The web session is committed before endpoint execution; rotation is not.
        self.db.commit.assert_called_once()
        self.assertEqual(self.client.cookies["remember_me"], "test-remember")

    def test_successful_remember_restore_commits_session_then_rotation(self) -> None:
        self.seed("remember")
        response = self.client.get("/protected")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.cookies["remember_me"], "new-remember")
        self.assertEqual(self.db.commit.call_count, 2)
        self.db.rollback.assert_not_called()

    def test_remember_rotation_uses_saved_identity_after_auth_session_closes(self) -> None:
        self.seed("remember")
        # A committed ORM user can no longer load expired attributes once detached.
        # Removing its ID here catches any post-close reliance on that instance.
        self.db.close.side_effect = lambda: self.user.__dict__.pop("id", None)
        response = self.client.get("/protected")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ns["create_remember_token"].call_args.kwargs["user_id"], 1)

    def test_session_refresh_database_failure_preserves_both_cookies(self) -> None:
        self.seed()
        self.client.cookies.set("remember_me", "test-remember")
        self.user.web_tokens[0]["expires_at"] = (
            datetime.now(UTC) + timedelta(minutes=10)
        ).isoformat()
        self.ns["validate_remember_token"].side_effect = TimeoutError()
        self.assert_temporary_error()
        self.assertEqual(self.client.cookies["session_token"], "test-session")
        self.assertEqual(self.client.cookies["remember_me"], "test-remember")
        self.assertEqual(self.calls, 0)

    def test_tracker_failure_is_not_misclassified_as_invalid_session_data(self) -> None:
        self.seed()
        self.ns["get_online_tracker"].return_value.update_activity.side_effect = TypeError()
        self.assert_temporary_error()
        self.db.execute.assert_not_called()
        self.assertEqual(self.calls, 0)

    def test_empty_database_still_redirects_to_setup(self) -> None:
        self.db.query.return_value.first.return_value = None
        response = self.client.get("/protected", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/setup")
        self.assertEqual(self.calls, 0)

    def test_public_post_failure_is_not_replayed(self) -> None:
        self.seed()
        self.failure = TimeoutError()
        self.assert_temporary_error("/login/failure", "POST")
        self.assertEqual(self.calls, 1)

    def test_public_endpoint_runs_once_when_setup_check_fails(self) -> None:
        self.seed()
        self.db.query.side_effect = ConnectionError()
        response = self.client.post("/login/failure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, 1)
        self.db.rollback.assert_called_once()

    def test_expired_session_still_requires_login(self) -> None:
        self.seed()
        self.user.web_tokens[0]["expires_at"] = (
            datetime.now(UTC) - timedelta(minutes=1)
        ).isoformat()
        response = self.client.get("/protected", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?reason=session_expired")
        self.assertNotIn("session_token", self.client.cookies)
        self.assertNotIn("session", self.client.cookies)
        self.assertEqual(self.calls, 0)

    def test_invalid_remember_cookie_still_expires(self) -> None:
        self.seed("remember")
        self.ns["validate_remember_token"].return_value = None
        response = self.client.get("/protected", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("remember_me", self.client.cookies)
        self.assertEqual(self.calls, 0)


class RememberTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        base = declarative_base()

        class TestUser(base):
            __tablename__ = "users"
            id = Column(Integer, primary_key=True)

        model_ns = load_module(ROOT / "models/remember_token.py", {"Base": base})
        self.model = model_ns["RememberToken"]
        self.ns = load_module(
            ROOT / "utils/remember_me.py", {"User": TestUser, "RememberToken": self.model}
        )
        self.ns["logger"] = Mock()
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        self.db.add(TestUser(id=1))
        self.db.commit()
        self.old_token = self.ns["create_remember_token"](self.db, 1, device_name="Test Browser")

    def test_atomic_rotation_rollback_keeps_old_token(self) -> None:
        self.ns["revoke_token"](self.db, self.old_token, commit=False)
        new_token = self.ns["create_remember_token"](
            self.db, 1, device_name="Test Browser", commit=False
        )
        self.db.rollback()
        old_hash = self.ns["hash_token"](self.old_token)
        self.assertIsNotNone(self.db.query(self.model).filter_by(token_hash=old_hash).first())
        new_hash = self.ns["hash_token"](new_token)
        self.assertIsNone(self.db.query(self.model).filter_by(token_hash=new_hash).first())

    def test_atomic_rotation_commit_replaces_old_token(self) -> None:
        self.ns["revoke_token"](self.db, self.old_token, commit=False)
        new_token = self.ns["create_remember_token"](
            self.db, 1, device_name="Test Browser", commit=False
        )
        self.db.commit()
        tokens = self.db.query(self.model).all()
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].token_hash, self.ns["hash_token"](new_token))

    def test_creation_failure_after_revocation_can_restore_old_token(self) -> None:
        self.ns["revoke_token"](self.db, self.old_token, commit=False)
        original_flush = self.db.flush
        self.db.flush = Mock(side_effect=ConnectionError("simulated insert failure"))
        try:
            with self.assertRaises(ConnectionError):
                self.ns["create_remember_token"](self.db, 1, commit=False)
        finally:
            self.db.rollback()
            self.db.flush = original_flush
        token = self.db.query(self.model).one()
        self.assertEqual(token.token_hash, self.ns["hash_token"](self.old_token))

    def test_default_revoke_remains_committed(self) -> None:
        self.assertTrue(self.ns["revoke_token"](self.db, self.old_token))
        self.db.rollback()
        self.assertEqual(self.db.query(self.model).count(), 0)


if __name__ == "__main__":
    unittest.main()
