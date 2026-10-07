"""Recording API regression tests; run with python -m unittest (no live cameras)."""

from __future__ import annotations

import unittest
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import Base, get_db
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.video import Video
from app.models.whitelist import WhatsappWhitelist, RoleEnum
from app.routes import videos
from app.routes import wa_webhook
from app.routes.auth import get_current_user
from app.utils.wa_bot_workflow import normalize_flow
from app.utils import video as video_utils
from starlette.requests import Request


class VideoRecordAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(
            self.engine,
            tables=[
                CameraGroup.__table__, Camera.__table__, camera_camera_groups,
                Video.__table__, WhatsappWhitelist.__table__,
            ],
        )
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.group = CameraGroup(name="Recording test group")
        self.other_group = CameraGroup(name="Other recording group")
        self.camera = Camera(
            hostname="CCTV-GATE-01", ip="192.168.1.100", groups=[self.group]
        )
        self.db.add_all([self.camera, self.other_group])
        self.db.commit()
        self.user = SimpleNamespace(
            username="api-operator", role="operator", group_id=self.group.id
        )

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="video-record-test")
        app.include_router(videos.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.recorder = self.enterContext(
            patch.object(videos, "record_video_and_save_db", new_callable=AsyncMock)
        )
        self.audit = self.enterContext(patch.object(videos, "log_audit"))

    def test_hostname_match_and_authenticated_audit(self) -> None:
        response = self.client.post(
            "/api/videos/record", json={"hostname": " cctv-gate-01 ", "duration": 30}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["camera_id"], self.camera.id)
        self.assertEqual(response.json()["duration"], 30)
        self.recorder.assert_awaited_once()
        self.assertEqual(
            self.recorder.call_args.kwargs, {"camera_id": self.camera.id, "duration": 30}
        )
        self.assertEqual(self.audit.call_args.kwargs["user"], "api-operator")

    def test_unique_ip_uses_default_duration(self) -> None:
        response = self.client.post("/api/videos/record", json={"ip": "192.168.1.100"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["duration"], 10)
        self.recorder.assert_awaited_once()

    def test_duplicate_ip_in_another_group_never_records(self) -> None:
        self.db.add(Camera(hostname="CCTV-OTHER", ip=self.camera.ip, groups=[self.other_group]))
        self.db.commit()
        response = self.client.post("/api/videos/record", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Multiple cameras match this IP address", response.json()["detail"])
        self.recorder.assert_not_called()
        self.audit.assert_not_called()
        # The unique hostname remains usable even when its IP is duplicated.
        response = self.client.post("/api/videos/record", json={"hostname": self.camera.hostname})
        self.assertEqual(response.status_code, 202)
        self.recorder.assert_awaited_once()

    def test_invalid_requests_never_record(self) -> None:
        for payload in (
            {},
            {"hostname": self.camera.hostname, "ip": self.camera.ip},
            {"hostname": "  "},
            {"ip": ""},
            {"hostname": self.camera.hostname, "duration": 4},
            {"ip": self.camera.ip, "duration": 61},
            {"ip": self.camera.ip, "duration": "invalid"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/videos/record", json=payload)
                self.assertEqual(response.status_code, 422)
        self.recorder.assert_not_called()
        self.audit.assert_not_called()

    def test_missing_camera_never_records(self) -> None:
        for payload in ({"hostname": "missing"}, {"ip": "192.168.1.101"}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/videos/record", json=payload)
                self.assertEqual(response.status_code, 404)
        self.recorder.assert_not_called()

    def test_group_access_applies_to_both_endpoints(self) -> None:
        self.user.group_id = self.other_group.id
        response = self.client.post("/api/videos/record", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 403)
        response = self.client.post(f"/videos/record/{self.camera.id}?duration=30")
        self.assertEqual(response.status_code, 403)
        self.recorder.assert_not_called()
        self.audit.assert_not_called()

    def test_unrestricted_user_can_record_by_id(self) -> None:
        self.user.group_id = None
        response = self.client.post(f"/videos/record/{self.camera.id}?duration=30")
        self.assertEqual(response.status_code, 202)
        self.recorder.assert_awaited_once()

    def test_viewer_cannot_record(self) -> None:
        self.user.role = "viewer"
        response = self.client.post("/api/videos/record", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 403)
        self.recorder.assert_not_called()

    def test_missing_authentication_cannot_record(self) -> None:
        del self.app.dependency_overrides[get_current_user]
        response = self.client.post("/api/videos/record", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 401)
        self.recorder.assert_not_called()

    def test_wait_endpoint_returns_completed_video(self) -> None:
        self.recorder.return_value = {
            "status": "success", "video_id": "recorded-video", "media_type": "video",
        }
        response = self.client.post(
            "/api/videos/record-and-wait", json={"hostname": self.camera.hostname, "duration": 30}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["video_id"], "recorded-video")
        self.assertEqual(self.recorder.call_args.kwargs["actor_username"], self.user.username)
        self.recorder.assert_awaited_once()

    def test_recording_pipeline_saves_video_and_returns_metadata_on_sqlite(self) -> None:
        self.camera.group = self.group
        self.db.commit()
        request = Request({"type": "http", "session": {}, "client": ("127.0.0.1", 1)})
        with tempfile.TemporaryDirectory() as folder:
            target = []

            def build_command(url: str, path: Path, duration: int, name: str) -> str:
                target.append(path)
                return "record test"

            def record_command(command: str) -> tuple:
                target[0].write_bytes(b"video fixture")
                return 0, "", ""

            with (
                patch.object(video_utils, "STATIC_VIDEO_DIR", Path(folder)),
                patch.object(video_utils, "SessionLocal", side_effect=lambda: Session(self.engine)),
                patch.object(video_utils, "is_reachable", return_value=True),
                patch.object(video_utils, "get_rtsp_url", return_value="rtsp://camera/test"),
                patch.object(video_utils, "detect_codec", return_value="h264"),
                patch.object(video_utils, "build_ffmpeg_watermark_cmd", side_effect=build_command),
                patch.object(video_utils, "_run_ffmpeg_sync", side_effect=record_command),
                patch.object(video_utils, "get_video_metadata", return_value={
                    "duration": 10, "size": 13, "width": 640, "height": 480,
                }),
                patch.object(video_utils, "generate_thumbnail", return_value=True),
                patch.object(video_utils, "get_ws_connections", return_value=[]),
                patch.object(video_utils, "log_audit") as audit,
            ):
                result = asyncio.run(video_utils.record_video_and_save_db(
                    request, self.camera.id, actor_username="api-operator"
                ))
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["media_type"], "video")
            saved = self.db.get(Video, result["video_id"])
            self.assertIsNotNone(saved)
            self.assertEqual(saved.camera_id, self.camera.id)
            self.assertEqual(len(saved.file_hash), 64)
            self.assertEqual(audit.call_args.kwargs["user"], "api-operator")

    def test_video_attachment_rejects_missing_and_outside_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "videos"
            root.mkdir()
            (Path(folder) / "outside.mp4").write_bytes(b"outside file")
            bot = wa_webhook.WABotHandler(self.db)
            command = {"response_video_source": "{{steps.data.body.video_id}}", "response": "Video"}
            for index, file_path in enumerate(("missing.mp4", "../outside.mp4")):
                video = Video(id=f"unsafe-{index}", camera_id=self.camera.id,
                              camera_name=self.camera.hostname, file_path=file_path)
                self.db.add(video)
                self.db.commit()
                bot.action_api_context = {"steps": {"data": {"body": {
                    "status": "success", "video_id": video.id, "camera_id": self.camera.id,
                }}}}
                with (
                    patch.object(wa_webhook, "STATIC_VIDEO_DIR", root),
                    patch.object(wa_webhook, "get_current_timezone", return_value="UTC"),
                    patch.object(bot, "_selected_api_token", return_value="test-token"),
                    patch.object(wa_webhook, "get_current_user", new=AsyncMock(return_value=self.user)),
                    self.assertRaises(ValueError),
                ):
                    asyncio.run(bot._attach_response_video(command))
            self.assertEqual(bot.outbound_items, [])

    def test_wait_endpoint_failure_returns_error(self) -> None:
        self.recorder.return_value = {"status": "error", "message": "Camera Offline"}
        response = self.client.post("/api/videos/record-and-wait", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Camera Offline")

    def test_configured_video_response_checks_group_access(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "video.mp4").write_bytes(b"video")
            self.db.add(Video(id="protected-video", camera_id=self.camera.id,
                              camera_name=self.camera.hostname, file_path="video.mp4"))
            self.db.commit()
            self.user.group_id = self.other_group.id
            bot = wa_webhook.WABotHandler(self.db)
            bot.action_api_context = {"steps": {"data": {"body": {
                "video_url": "/api/videos/secure/protected-video",
            }}}}
            with (
                patch.object(wa_webhook, "STATIC_VIDEO_DIR", root),
                patch.object(wa_webhook, "get_current_timezone", return_value="UTC"),
                patch.object(bot, "_selected_api_token", return_value="test-token"),
                patch.object(wa_webhook, "get_current_user", new=AsyncMock(return_value=self.user)),
                self.assertRaisesRegex(ValueError, "Access denied"),
            ):
                asyncio.run(bot._attach_response_video({
                    "response_video_source": "{{steps.data.body.video_url}}",
                }))
            self.assertEqual(bot.outbound_items, [])

    def test_wait_endpoint_rejects_duplicate_ip_and_wrong_group(self) -> None:
        self.db.add(Camera(hostname="OTHER", ip=self.camera.ip, groups=[self.other_group]))
        self.db.commit()
        response = self.client.post("/api/videos/record-and-wait", json={"ip": self.camera.ip})
        self.assertEqual(response.status_code, 409)
        self.user.group_id = self.other_group.id
        response = self.client.post(
            "/api/videos/record-and-wait", json={"hostname": self.camera.hostname}
        )
        self.assertEqual(response.status_code, 403)
        self.recorder.assert_not_called()

    def test_api_flow_attaches_completed_video_with_caption(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video_path = root / "recorded.mp4"
            video_path.write_bytes(b"test video")
            self.db.add_all([
                Video(id="recorded-video", camera_id=self.camera.id,
                      camera_name=self.camera.hostname, file_path="recorded.mp4"),
                WhatsappWhitelist(phone_number="628123456789", role=RoleEnum.admin),
            ])
            self.db.commit()
            self.recorder.return_value = {
                "status": "success", "video_id": "recorded-video", "media_type": "video",
                "camera_id": self.camera.id, "hostname": self.camera.hostname, "duration": 30,
            }
            command = {
                "trigger": "/record", "action": "api_flow", "role": "admin",
                "response": "Video {{steps.data.body.hostname}}",
                "response_type": "video",
                "response_video_source": "{{steps.data.body.video_id}}",
                "flow": normalize_flow([{
                    "name": "data", "path": "/api/videos/record-and-wait", "method": "POST",
                    "body": {"hostname": "{{argument}}", "duration": 30},
                }]),
                "processing_messages": [{"text": "Recording {{argument}}"}],
            }
            bot = wa_webhook.WABotHandler(self.db)
            progress = AsyncMock(return_value={"success": True})
            bot.progress_sender = progress
            with (
                patch.dict(sys.modules, {"app.main": SimpleNamespace(app=self.app)}),
                patch.object(wa_webhook, "get_command_settings", return_value=[command]),
                patch.object(wa_webhook, "match_command", return_value=(command, self.camera.hostname)),
                patch.object(wa_webhook, "get_current_timezone", return_value="Asia/Makassar"),
                patch.object(wa_webhook, "STATIC_VIDEO_DIR", root),
                patch.object(wa_webhook, "log_audit"),
                patch.object(bot, "_selected_api_token", return_value="test-token"),
                patch.object(wa_webhook, "get_current_user", new=AsyncMock(return_value=self.user)),
            ):
                asyncio.run(bot.handle("628123456789", "/record CCTV-GATE-01", is_group=True))
                command.pop("response_type")
                text_bot = wa_webhook.WABotHandler(self.db)
                text_bot.progress_sender = AsyncMock()
                with patch.object(text_bot, "_selected_api_token", return_value="test-token"):
                    asyncio.run(text_bot.handle("628123456789", "/record CCTV-GATE-01", is_group=True))
                self.assertEqual(text_bot.outbound_items, [])
                assert not text_bot.private_response
            self.assertFalse(bot.private_response)
            self.assertFalse(bot.action_failed)
            progress.assert_awaited_once()
            self.assertEqual(bot.outbound_items, [{"video_path": str(video_path), "text": "Video CCTV-GATE-01"}])
            gateway = Mock()
            wa_webhook._send_wa_message_item(gateway, "record-group@g.us", bot.outbound_items[0], "command-id")
            gateway.send_video_file.assert_called_once_with(
                "record-group@g.us", str(video_path), "Video CCTV-GATE-01", reply_to="command-id"
            )
            gateway.send_image_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
