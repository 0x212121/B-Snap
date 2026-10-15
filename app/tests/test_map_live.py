"""Live source authorization, bounded processes, and real multipart output."""

from __future__ import annotations

import asyncio
import io
import subprocess
import threading

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.main import error_wrapper_middleware
from app.models.camera import Camera, camera_camera_groups
from app.models.camera_group import CameraGroup
from app.models.config import Configuration
from app.routes import auth, map_live
from app.utils import camera_onvif, live_stream


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the full schema; access checks use only compatible SQLite tables."""


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(
        engine,
        tables=[
            CameraGroup.__table__,
            Camera.__table__,
            camera_camera_groups,
            Configuration.__table__,
        ],
    )
    db = Session(engine)
    first = CameraGroup(id=101, name="Site A")
    second = CameraGroup(id=102, name="Site B")
    db.add_all(
        [
            first,
            second,
            Camera(
                id="shared",
                hostname="shared",
                status="Active",
                ip="192.0.2.1",
                group=second,
                groups=[first, second],
            ),
            Camera(id="other", hostname="other", status="Active", ip="192.0.2.2", groups=[second]),
            Camera(id="maintenance", hostname="maintenance", status="Maintenance", groups=[first]),
        ]
    )
    db.add(Configuration(key="map_live_enabled", value="1"))
    db.commit()
    audit = Mock()
    source = Mock(return_value="rtsp://user:secret@camera.example/live")
    preview = Mock()
    preview.first_frame.return_value = b"--bsnap-live\r\n"

    async def chunks(first: bytes):
        yield first

    preview.chunks.side_effect = chunks
    factory = Mock(return_value=preview)
    release = Mock()
    reserve = Mock(return_value=release)
    monkeypatch.setattr(map_live, "get_rtsp_url", source)
    monkeypatch.setattr(map_live, "LivePreview", factory)
    monkeypatch.setattr(map_live, "reserve_viewer", reserve)
    monkeypatch.setattr(map_live, "log_audit", audit)
    app = FastAPI()
    app.middleware("http")(error_wrapper_middleware)
    app.include_router(map_live.router)
    app.dependency_overrides[map_live.get_db] = lambda: db
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(
        id="user", username="test", group_id=101, role="admin"
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            db=db,
            source=source,
            factory=factory,
            preview=preview,
            release=release,
            audit=audit,
            reserve=reserve,
        )
    db.close()
    engine.dispose()


def test_live_access_uses_camera_memberships_and_hides_credentials(live_client) -> None:
    response = live_client.client.get("/maps/cameras/shared/live")
    assert response.status_code == 200
    assert "boundary=bsnap-live" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text
    assert "secret" not in str(live_client.audit.call_args)
    assert live_client.audit.call_args.kwargs["action"] == "view_camera_live"
    assert live_client.source.call_args.kwargs["rtsp_port"] == 554


@pytest.mark.parametrize(
    ("camera_id", "status"), [("other", 404), ("missing", 404), ("maintenance", 409)]
)
def test_rejected_access_never_starts_camera_io(live_client, camera_id: str, status: int) -> None:
    assert live_client.client.get(f"/maps/cameras/{camera_id}/live").status_code == status
    live_client.source.assert_not_called()
    live_client.factory.assert_not_called()
    live_client.reserve.assert_not_called()


def test_all_groups_user_can_open_other_camera(live_client) -> None:
    live_client.client.app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(
        id="all", username="all", group_id=None, role="admin"
    )
    assert live_client.client.get("/maps/cameras/other/live").status_code == 200


def test_authentication_required(live_client) -> None:
    def anonymous():
        raise HTTPException(status_code=401)

    live_client.client.app.dependency_overrides[auth.get_current_user] = anonymous
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 401
    live_client.factory.assert_not_called()


@pytest.mark.parametrize("role", ["user", "viewer", "operator"])
@pytest.mark.parametrize("path", ["/maps/cameras/shared/live", "/maps/live/status"])
def test_non_administrators_cannot_use_live_view(live_client, role: str, path: str) -> None:
    live_client.client.app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(
        id="denied", role=role, group_id=None
    )
    assert live_client.client.get(path, headers={"Accept": "application/json"}).status_code == 403
    live_client.source.assert_not_called()
    live_client.reserve.assert_not_called()
    live_client.factory.assert_not_called()


def test_config_off_prevents_live_camera_io(live_client) -> None:
    setting = live_client.db.get(Configuration, "map_live_enabled")
    setting.value = "0"
    live_client.db.commit()
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 403
    assert live_client.client.get("/maps/live/status").json() == {"enabled": False}
    live_client.source.assert_not_called()
    live_client.factory.assert_not_called()
    setting.value = "1"
    live_client.db.commit()
    assert live_client.client.get("/maps/live/status").json() == {"enabled": True}
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 200


def test_active_stream_stops_when_policy_is_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    process.stdout.read1.return_value = b"frame"
    monkeypatch.setattr(live_stream.subprocess, "Popen", lambda *_args, **_kwargs: process)
    clock = iter([0, 6])
    monkeypatch.setattr(live_stream, "monotonic", lambda: next(clock))
    release = Mock()
    policy = Mock(return_value=False)
    preview = live_stream.LivePreview("rtsp://camera/live", release, allowed=policy)

    async def read() -> list[bytes]:
        return [chunk async for chunk in preview.chunks(b"first")]

    assert asyncio.run(read()) == [b"first"]
    policy.assert_called_once()
    process.kill.assert_called_once()
    release.assert_called_once()


def test_hls_source_reuses_credentials_without_rtsp_probe(
    live_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        map_live,
        "_hls_recording_url",
        lambda _camera: "https://user:secret@camera.example/live.m3u8?profile=main",
    )
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 200
    live_client.source.assert_not_called()
    assert live_client.factory.call_args.args[0].endswith("?profile=main")


def test_source_failure_releases_capacity(live_client) -> None:
    live_client.source.return_value = None
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 502
    live_client.release.assert_called_once()


def test_first_frame_failure_closes_process_and_never_audits_success(live_client) -> None:
    live_client.preview.first_frame.side_effect = HTTPException(status_code=502)
    assert live_client.client.get("/maps/cameras/shared/live").status_code == 502
    live_client.preview.close.assert_called_once()
    live_client.audit.assert_not_called()


def test_viewer_slots_are_bounded_and_release_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_stream, "_viewers", {})
    releases = [live_stream.reserve_viewer("one") for _ in range(live_stream.MAX_USER_STREAMS)]
    with pytest.raises(HTTPException) as caught:
        live_stream.reserve_viewer("one")
    assert caught.value.status_code == 429
    for release in releases:
        release()
        release()
    assert live_stream._viewers == {}


def test_missing_ffmpeg_releases_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("path should not be exposed")

    monkeypatch.setattr(live_stream.subprocess, "Popen", unavailable)
    release = Mock()
    with pytest.raises(HTTPException) as caught:
        live_stream.LivePreview("rtsp://camera/live", release)
    assert caught.value.status_code == 503
    assert "path" not in caught.value.detail
    release.assert_called_once()


def test_cancellation_during_pipe_read_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped = threading.Event()
    process = Mock()
    process.poll.return_value = None
    process.kill.side_effect = stopped.set
    process.wait.return_value = 0

    def blocking_read(_size: int) -> bytes:
        stopped.wait(timeout=5)
        return b""

    process.stdout.read1.side_effect = blocking_read
    monkeypatch.setattr(live_stream.subprocess, "Popen", lambda *_args, **_kwargs: process)
    release = Mock()
    preview = live_stream.LivePreview("rtsp://camera/live", release)

    async def disconnect() -> None:
        stream = preview.chunks(b"first")
        assert await anext(stream) == b"first"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), timeout=0.05)

    asyncio.run(disconnect())
    assert stopped.is_set()
    release.assert_called_once()


def test_empty_source_output_closes_and_returns_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    process.stdout.read1.return_value = b""
    monkeypatch.setattr(live_stream.subprocess, "Popen", lambda *_args, **_kwargs: process)
    release = Mock()
    preview = live_stream.LivePreview("rtsp://user:secret@camera/live", release)
    with pytest.raises(HTTPException) as caught:
        preview.first_frame()
    assert caught.value.status_code == 502
    assert "secret" not in caught.value.detail
    release.assert_called_once()


def test_rtsp_port_override_keeps_the_configured_onvif_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = SimpleNamespace(
        id="probe", username="test", password="test-only", ip="192.0.2.1", port=80
    )
    capture = Mock()
    capture.isOpened.return_value = False
    open_capture = Mock(return_value=capture)
    onvif = Mock()
    onvif.return_value.create_media_service.return_value.GetProfiles.side_effect = RuntimeError(
        "test probe"
    )
    monkeypatch.setattr(camera_onvif, "open_capture", open_capture)
    monkeypatch.setattr(camera_onvif, "ONVIFCamera", onvif)
    assert camera_onvif.get_rtsp_url(camera, rtsp_port=554) is None
    assert all(":554/Streaming/" in call.args[0] for call in open_capture.call_args_list)
    assert onvif.call_args.args[1] == 80


def test_protocol_specific_live_timeouts() -> None:
    rtsp = live_stream.build_live_command("rtsp://camera.example/live")
    hls = live_stream.build_live_command("https://camera.example/live.m3u8")
    assert "-timeout" in rtsp
    assert "-rw_timeout" not in rtsp
    assert "-rw_timeout" in hls
    assert "-rtsp_transport" not in hls


def test_explicit_rtsp_source_preserves_port_path_and_query() -> None:
    camera = SimpleNamespace(
        snapshot_url="rtsp://192.0.2.1:7070/custom?stream=1",
        username="test@user",
        password="test#pass",
    )
    source = live_stream.configured_rtsp_source(camera)
    assert source == "rtsp://test%40user:test%23pass@192.0.2.1:7070/custom?stream=1"


def test_live_onvif_retry_uses_camera_time_after_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = SimpleNamespace(
        id="clock", username="test", password="test-only", ip="192.0.2.1", port=80
    )
    cap = Mock()
    cap.isOpened.return_value = False
    media = Mock()
    media.GetProfiles.return_value = [SimpleNamespace(token="main")]
    media.GetStreamUri.return_value = SimpleNamespace(Uri="rtsp://192.0.2.1:7070/stream1")
    device = Mock()
    device.create_media_service.return_value = media
    onvif = Mock(side_effect=[RuntimeError("Sender not Authorized"), device])
    monkeypatch.setattr(camera_onvif, "open_capture", lambda _url: cap)
    monkeypatch.setattr(camera_onvif, "ONVIFCamera", onvif)
    source = camera_onvif.get_rtsp_url(camera, rtsp_port=554, try_acti=True)
    assert source is not None
    assert urlparse(source).port == 7070
    assert [call.kwargs["adjust_time"] for call in onvif.call_args_list] == [False, True]


def test_acti_live_fallback_after_onvif_failure_releases_every_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = SimpleNamespace(
        id="acti", username="test", password="test-only", ip="192.0.2.1", port=80
    )
    probes = []

    def capture(url: str):
        parsed = urlparse(url)
        cap = Mock()
        cap.isOpened.return_value = parsed.port == 7070 and parsed.path == "/stream1"
        probes.append(cap)
        return cap

    onvif = Mock(side_effect=RuntimeError("Sender not Authorized"))
    monkeypatch.setattr(camera_onvif, "open_capture", capture)
    monkeypatch.setattr(camera_onvif, "ONVIFCamera", onvif)
    source = camera_onvif.get_rtsp_url(camera, rtsp_port=554, try_acti=True)
    assert source is not None
    assert urlparse(source).port == 7070
    assert urlparse(source).path == "/stream1"
    for cap in probes:
        cap.release.assert_called_once()


def test_real_ffmpeg_produces_frames_and_generator_close_reaps_process(tmp_path: Path) -> None:
    executable = live_stream._ffmpeg_executable()
    if not Path(executable).exists():
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x96:rate=10",
            "-t",
            "3",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    release = Mock()
    preview = live_stream.LivePreview(str(source), release)
    first = preview.first_frame()
    assert b"--bsnap-live" in first
    assert b"Content-type: image/jpeg" in first
    jpeg = first[first.index(b"\xff\xd8") : first.index(b"\xff\xd9") + 2]
    assert Image.open(io.BytesIO(jpeg)).size == (128, 96)

    async def close_client():
        stream = preview.chunks(first)
        await anext(stream)
        await stream.aclose()

    asyncio.run(close_client())
    assert preview.process.poll() is not None
    release.assert_called_once()
