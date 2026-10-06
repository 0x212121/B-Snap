"""Exercise production exception handler precedence without starting services."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app import main
from app.db.database import get_db
from app.routes.auth import operator_access_required
from app.routes.videos import router
from app.routes import wa_webhook
from app.routes import snap_gallery
from starlette.middleware.sessions import SessionMiddleware
from app.routes.auth import admin_access_required
from app.utils.wa_bot_workflow import APIFlowError


class APIErrorHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI(exception_handlers=main.app.exception_handlers.copy())
        app.add_middleware(SessionMiddleware, secret_key="capture-api-test")
        app.include_router(router)
        app.include_router(wa_webhook.router)
        app.include_router(snap_gallery.router)
        app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(id=1, api_tokens=[])
        self.db = Mock()
        self.db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[operator_access_required] = lambda: SimpleNamespace(
            username="test", role="operator", group_id=1
        )

        @app.get("/forbidden-page")
        async def forbidden() -> None:
            raise HTTPException(status_code=403, detail="Camera group access denied")

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_unknown_camera_returns_original_json_error(self) -> None:
        response = self.client.post(
            "/api/videos/record-and-wait", json={"hostname": "unknown-camera"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"status": "error", "detail": "Camera not found"})
        self.assertIn("application/json", response.headers["content-type"])

    def test_missing_api_route_is_json_without_accept_header(self) -> None:
        response = self.client.get("/api/nonexistent-route")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Not Found")

    def test_capture_api_error_and_success_are_json(self) -> None:
        camera = SimpleNamespace(id="camera-id", hostname="capture-test", status="Active",
                                 groups=[SimpleNamespace(id=1)])
        snapshot = SimpleNamespace(id="snapshot-id", file_path="test.jpg", resolution="640x480")
        with patch.object(snap_gallery, "log_audit"):
            for result, expected in ((None, 404), (camera, 500), (camera, 200)):
                with self.subTest(expected=expected):
                    self.db.query.return_value.filter.return_value.first.return_value = result
                    with patch.object(snap_gallery.SnapshotService, "capture_snapshot", new=AsyncMock(
                        return_value=snapshot if expected == 200 else None
                    )):
                        response = self.client.post("/api/snapshots/capture/capture-test")
                    self.assertEqual(response.status_code, expected)
                    self.assertIn("application/json", response.headers["content-type"])
                    self.assertTrue(response.json()["message"])
                    self.assertEqual(response.json()["success"], expected == 200)

    def test_builder_test_returns_error_node_variables(self) -> None:
        context = {"steps": {"data": {
            "status_code": 404, "body": {"status": "error", "detail": "Camera not found"},
        }}}
        runner = AsyncMock(side_effect=APIFlowError("API node data returned HTTP 404", context))
        with patch.object(wa_webhook, "run_api_flow", runner):
            response = self.client.post("/api/admin/wa-bot/test-flow", json={
                "sender": "628123456789",
                "flow": [{"name": "data", "path": "/api/videos/record-and-wait", "method": "POST"}],
            })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["steps"], context["steps"])
        self.assertEqual(response.json()["status"], "error")

    def test_capture_rejects_duplicate_ip_and_denied_group_without_capturing(self) -> None:
        with (
            patch.object(snap_gallery, "log_audit"),
            patch.object(snap_gallery.SnapshotService, "capture_snapshot", new_callable=AsyncMock) as capture,
        ):
            self.db.query.return_value.filter.return_value.limit.return_value.all.return_value = [Mock(), Mock()]
            response = self.client.post("/api/snapshots/capture/192.168.1.5")
            self.assertEqual(response.status_code, 409)
            self.db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(groups=[])
            response = self.client.post("/api/snapshots/capture/restricted")
            self.assertEqual(response.status_code, 403)
            capture.assert_not_awaited()

    def test_json_forbidden_preserves_error_detail(self) -> None:
        response = self.client.get("/forbidden-page", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Camera group access denied")

    def test_api_forbidden_is_json_even_when_client_accepts_html(self) -> None:
        camera = SimpleNamespace(groups=[])
        self.db.query.return_value.filter.return_value.limit.return_value.all.return_value = [camera]
        response = self.client.post(
            "/api/videos/record-and-wait", json={"hostname": "restricted-camera"},
            headers={"Accept": "text/html"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Access denied to this camera")

    def test_browser_page_errors_still_render_templates(self) -> None:
        def render(name: str, context: dict, status_code: int) -> HTMLResponse:
            return HTMLResponse(name, status_code=status_code)

        with patch.object(main.templates, "TemplateResponse", side_effect=render) as renderer:
            for path, code, template in (
                ("/nonexistent-page", 404, "404.html"),
                ("/forbidden-page", 403, "403.html"),
            ):
                with self.subTest(path=path):
                    response = self.client.get(path, headers={"Accept": "text/html"})
                    self.assertEqual(response.status_code, code)
                    self.assertIn("text/html", response.headers["content-type"])
                    self.assertEqual(renderer.call_args.args[0], template)


if __name__ == "__main__":
    unittest.main()
