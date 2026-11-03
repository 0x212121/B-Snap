"""Isolated archive tests: synthetic SQLite schema, local media, no real camera jobs.

Only selected tables are cloned; JSONB becomes JSON and history IDs become SQLite
INTEGER in this test schema. Production metadata and PostgreSQL types stay intact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import socket
import subprocess
import sys
import threading

from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import NAMESPACE_URL, uuid4, uuid5

import psutil
import pytest

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import JSON, Integer, MetaData, create_engine, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from app.db.database import Base, get_db
from app.jobs import recording_archive as archives, recording_process
from app.middleware.csrf import CSRFMiddleware
from app.models.camera import Camera
from app.models.camera_group import CameraGroup
from app.models.job_execution_log import JobExecutionLog
from app.models.recording_schedule import CameraRecordingState, RecordingSchedule
from app.models.user import User
from app.models.video import Video
from app.routes import recording_schedules as routes, videos
from app.routes.auth import get_current_user
from app.schemas.recording_schedule import RecordingSchedulePayload, next_clip_run, window_end
from app.utils import recording_lease as leases, video as video_utils
from app.utils.video_dates import filter_video_dates


def payload(**changes) -> dict:
    return {"camera_id": "camera", "name": "Gate archive", **changes}


@pytest.fixture
def archive_db(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata = MetaData()
    for name in (
        "camera_groups",
        "cameras",
        "camera_camera_groups",
        "users",
        "recording_schedules",
        "camera_recording_states",
        "videos",
        "job_execution_logs",
        "audit_logs",
    ):
        Base.metadata.tables[name].to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    metadata.tables["job_execution_logs"].c.id.type = Integer()
    metadata.tables["audit_logs"].c.id.type = Integer()
    metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(archives, "SessionLocal", factory)
    monkeypatch.setattr(leases, "SessionLocal", factory)
    monkeypatch.setattr(archives, "STATIC_VIDEO_DIR", tmp_path / "videos")
    monkeypatch.setattr(archives, "get_config", lambda _key, default: default)
    monkeypatch.setattr(routes, "get_config", lambda _key, default: default)
    monkeypatch.setattr(routes, "get_current_timezone", lambda _db: "Asia/Makassar")
    monkeypatch.setattr(archives, "get_current_timezone", lambda _db: "Asia/Makassar")
    with factory() as db:
        group = CameraGroup(name="Archive access")
        other = CameraGroup(name="Other cameras")
        db.add_all([group, other])
        db.flush()
        user = User(username="archive-admin", password="unused", role="admin", group_id=group.id)
        camera = Camera(
            id="camera", hostname="GATE-01", status="Active", group_id=other.id, groups=[group]
        )
        outside = Camera(
            id="outside", hostname="OTHER-01", status="Active", group_id=group.id, groups=[other]
        )
        db.add_all([user, camera, outside])
        db.commit()
        yield SimpleNamespace(
            db=db, factory=factory, user=user, camera=camera, outside=outside, engine=engine
        )
    engine.dispose()


@pytest.fixture
def client(archive_db):
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="isolated-archive-tests")
    app.include_router(routes.router)
    app.include_router(videos.router)

    @app.get("/test-session")
    def set_session(request: Request):
        request.session["user_id"] = archive_db.user.id
        return {"status": "ok"}

    app.dependency_overrides[get_db] = lambda: archive_db.db
    app.dependency_overrides[get_current_user] = lambda: archive_db.user
    with TestClient(
        app, headers={"Origin": "http://testserver", "Accept": "application/json"}
    ) as test_client:
        test_client.get("/test-session")
        yield test_client


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "bad"},
        {"clip_seconds": 4},
        {"clip_seconds": 3601},
        {"segment_seconds": 0},
        {"interval_minutes": 0},
        {"weekdays": []},
        {"weekdays": [0, 0]},
        {"weekdays": [7]},
        {"start_time": "25:00"},
        {"mode": "window", "start_time": "08:00", "end_time": "08:00"},
        {"interval_minutes": 1, "clip_seconds": 120},
        {"cron": "bad cron"},
        {"cron": "0 0 31 2 *"},
        {"unknown": "value"},
    ],
)
def test_invalid_settings(changes):
    with pytest.raises(ValidationError):
        RecordingSchedulePayload(**payload(**changes))


@pytest.mark.parametrize(
    ("zone", "offset"), [("Asia/Jakarta", 7), ("Asia/Makassar", 8), ("Asia/Jayapura", 9)]
)
def test_overnight_window_uses_start_weekday_and_local_midnight(zone, offset):
    settings = RecordingSchedulePayload(
        **payload(mode="window", weekdays=[0], start_time="22:00", end_time="06:00")
    )
    # Monday night continuing into Tuesday in each configured Indonesian timezone.
    now = datetime(2026, 11, 3, 1, tzinfo=UTC) - timedelta(hours=offset)
    assert window_end(settings, now, zone) == datetime(2026, 11, 3, 6, tzinfo=UTC) - timedelta(
        hours=offset
    )
    assert window_end(settings, now + timedelta(hours=5), zone) is None
    assert window_end(settings, now + timedelta(days=1), zone) is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 1, 5, 45, tzinfo=UTC), datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
        (datetime(2026, 11, 1, 6, 15, tzinfo=UTC), datetime(2026, 11, 1, 6, 30, tzinfo=UTC)),
        (datetime(2026, 3, 8, 6, 45, tzinfo=UTC), datetime(2026, 3, 8, 7, 30, tzinfo=UTC)),
    ],
)
def test_window_dst_includes_ambiguous_end_and_rolls_nonexistent_end_forward(now, expected):
    settings = RecordingSchedulePayload(
        **payload(
            mode="window", start_time="00:00", end_time="01:30" if now.month == 11 else "02:30"
        )
    )
    assert window_end(settings, now, "America/New_York") == expected


def test_clip_next_run_coalesces_and_uses_configured_timezone():
    now = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    settings = RecordingSchedulePayload(**payload(cron="0 8 * * *"))
    assert next_clip_run(settings, now, "Asia/Makassar") == now + timedelta(days=1)
    assert next_clip_run(settings, now, "Asia/Jakarta") == now + timedelta(hours=1)
    settings.cron = ""
    assert next_clip_run(settings, now, "Asia/Jayapura") == now + timedelta(minutes=15)


def test_crud_membership_pagination_and_retained_videos(client, archive_db):
    result = client.post("/admin/recording-schedules/api", json=payload(enabled=True))
    assert result.status_code == 201
    schedule_id = result.json()["id"]
    listing = client.get("/admin/recording-schedules/api").json()
    assert listing["timezone"] == "Asia/Makassar"
    assert listing["items"][0]["next_run_at"].endswith("+00:00")
    assert {camera["id"] for camera in listing["cameras"]} == {"camera"}
    assert (
        client.post("/admin/recording-schedules/api", json=payload(camera_id="outside")).status_code
        == 403
    )
    edited = client.put(
        f"/admin/recording-schedules/api/{schedule_id}",
        json=payload(mode="continuous", enabled=False),
    )
    assert edited.status_code == 200
    assert archive_db.db.get(RecordingSchedule, schedule_id).next_run_at is None
    for index in range(11):
        assert (
            client.post(
                "/admin/recording-schedules/api", json=payload(name=f"Archive {index:02}")
            ).status_code
            == 201
        )
    page = client.get("/admin/recording-schedules/api?page=2&page_size=5").json()
    assert (page["total"], page["page"], page["pages"], len(page["items"])) == (12, 2, 3, 5)
    archive_db.db.add(
        Video(id="keep", camera_id="camera", camera_name="GATE-01", file_path="evidence.mp4")
    )
    archive_db.db.commit()
    assert client.delete(f"/admin/recording-schedules/api/{schedule_id}").status_code == 200
    assert archive_db.db.get(Video, "keep") is not None


def test_admin_auth_csrf_and_existing_schedule_access(client, archive_db):
    schedule_id = client.post("/admin/recording-schedules/api", json=payload()).json()["id"]
    archive_db.user.group_id = 999
    assert client.get("/admin/recording-schedules/api").json()["total"] == 0
    assert (
        client.put(f"/admin/recording-schedules/api/{schedule_id}", json=payload()).status_code
        == 403
    )
    assert client.delete(f"/admin/recording-schedules/api/{schedule_id}").status_code == 403
    archive_db.user.role = "operator"
    assert client.get("/admin/recording-schedules/api").status_code == 403
    assert client.post("/admin/recording-schedules/api", json=payload()).status_code == 403
    archive_db.user.role = "admin"
    response = client.post(
        "/admin/recording-schedules/api",
        json=payload(),
        headers={"Origin": "https://foreign.invalid"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_REJECTED"


def test_camera_lease_exclusion_expiry_and_stale_owner(archive_db):
    first = leases.acquire_recording_lease("camera", "schedule-one")
    assert first
    assert leases.acquire_recording_lease("camera", "schedule-two") is None
    assert leases.acquire_recording_lease("camera", seconds=leases.MANUAL_LEASE_SECONDS) is None
    assert leases.renew_recording_lease("camera", first)
    with archive_db.factory() as db:
        db.get(CameraRecordingState, "camera").lease_until = datetime.now(UTC) - timedelta(
            seconds=1
        )
        db.commit()
    assert not leases.renew_recording_lease("camera", first)
    second = leases.acquire_recording_lease("camera", "schedule-two")
    assert second
    assert second != first
    leases.release_recording_lease("camera", first)
    assert leases.acquire_recording_lease("camera") is None
    leases.release_recording_lease("camera", second, "Retry later")
    assert leases.acquire_recording_lease("camera", "schedule-three") is None
    assert leases.acquire_recording_lease("camera")


def test_runtime_rechecks_permission_disable_and_timezone(client, archive_db, monkeypatch):
    schedule_id = client.post(
        "/admin/recording-schedules/api", json=payload(mode="continuous", enabled=True)
    ).json()["id"]
    row = archive_db.db.get(RecordingSchedule, schedule_id)
    plan = archives.load_plan(archive_db.db, row)
    session = archives.RecordingSession(plan, "owner")
    assert session.current()
    monkeypatch.setattr(archives, "get_current_timezone", lambda _db: "Asia/Jakarta")
    assert not session.current()
    monkeypatch.setattr(archives, "get_current_timezone", lambda _db: "Asia/Makassar")
    archive_db.user.role = "operator"
    archive_db.db.commit()
    assert not session.current()
    archive_db.user.role = "admin"
    row.enabled = False
    archive_db.db.commit()
    assert not session.current()


def test_dispatch_is_nonblocking_prioritizes_continuous_and_skips_overlap(
    client, archive_db, monkeypatch
):
    client.post("/admin/recording-schedules/api", json=payload(enabled=True))
    client.post("/admin/recording-schedules/api", json=payload(mode="continuous", enabled=True))
    starts = []

    class FakeSession:
        def __init__(self, plan, owner):
            self.owner = owner
            self.thread = Mock()
            self.thread.start.side_effect = lambda: starts.append(plan.settings.mode)
            self.thread.is_alive.return_value = True

    monkeypatch.setattr(archives, "RecordingSession", FakeSession)
    runtime = archives.ArchiveRuntime()
    runtime.dispatch()
    runtime.dispatch()
    assert starts == ["continuous"]
    archive_db.db.expire_all()
    clips = archive_db.db.query(RecordingSchedule).filter_by(mode="clips").one()
    assert clips.next_run_at > datetime.now(UTC).replace(tzinfo=None)


def test_manual_recording_reports_busy_before_contacting_camera(archive_db, monkeypatch):
    assert leases.acquire_recording_lease("camera", "schedule")
    monkeypatch.setattr(video_utils, "acquire_recording_lease", leases.acquire_recording_lease)
    camera_lookup = Mock(side_effect=AssertionError("Busy camera must not be contacted"))
    monkeypatch.setattr(video_utils, "SessionLocal", camera_lookup)
    result = asyncio.run(video_utils.record_video_and_save_db(Mock(), "camera"))
    assert result == {"status": "error", "message": "Camera is already recording"}


def test_cron_timezone_reload_recomputes_persisted_next_run(client, archive_db, monkeypatch):
    schedule_id = client.post(
        "/admin/recording-schedules/api", json=payload(enabled=True, cron="0 8 * * *")
    ).json()["id"]
    row = archive_db.db.get(RecordingSchedule, schedule_id)
    row.next_run_at = datetime(2026, 11, 4, 0, tzinfo=UTC)
    row.next_run_timezone = "Asia/Makassar"
    archive_db.db.commit()

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, 12, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(archives, "datetime", FixedClock)
    monkeypatch.setattr(archives, "get_current_timezone", lambda _db: "Asia/Jakarta")
    archives.ArchiveRuntime().dispatch()
    archive_db.db.expire_all()
    assert row.next_run_at == datetime(2026, 11, 4, 1)
    assert row.next_run_timezone == "Asia/Jakarta"


@pytest.mark.parametrize("failure", ["lease", "storage"])
def test_heartbeat_fences_lease_loss_and_low_storage(failure, monkeypatch):
    settings = RecordingSchedulePayload(**payload(mode="continuous"))
    plan = archives.RecordingPlan("schedule", settings, datetime.now(UTC), "UTC", None)
    session = archives.RecordingSession(plan, "owner")
    process = Mock()
    monkeypatch.setattr(archives, "renew_recording_lease", lambda *_args: failure != "lease")
    monkeypatch.setattr(archives, "get_config", lambda _key, default: default)
    monkeypatch.setattr(archives.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    with pytest.raises(RuntimeError):
        session.heartbeat(process)
    process.stdin.write.assert_not_called()


def test_dispatch_respects_capacity_storage_and_shutdown(client, archive_db, monkeypatch):
    client.post("/admin/recording-schedules/api", json=payload(mode="continuous", enabled=True))
    acquire = Mock(
        side_effect=AssertionError("A resource-limited worker must not acquire a camera")
    )
    monkeypatch.setattr(archives, "acquire_recording_lease", acquire)
    runtime = archives.ArchiveRuntime()
    active = SimpleNamespace(thread=Mock(), stop=Mock())
    active.thread.is_alive.return_value = True
    runtime.sessions["outside"] = active
    monkeypatch.setattr(
        archives,
        "get_config",
        lambda key, default: 1 if key == "recording_max_cameras" else default,
    )
    runtime.dispatch()
    runtime.sessions.clear()
    monkeypatch.setattr(archives.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    runtime.dispatch()
    runtime.sessions["outside"] = active
    runtime.shutdown()
    active.stop.set.assert_called_once()
    active.thread.join.assert_called_once_with(timeout=30)
    runtime.dispatch()
    acquire.assert_not_called()


def test_segment_commit_hash_recovery_and_deduplication(archive_db, monkeypatch):
    key = uuid5(NAMESPACE_URL, "camera").hex
    directory = archives.STATIC_VIDEO_DIR / ".recording" / key / uuid4().hex
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(
        json.dumps({"camera_id": "camera", "schedule_id": "test-schedule"})
    )
    (directory / "00000000.mp4").write_bytes(b"synthetic-evidence")
    monkeypatch.setattr(
        archives, "get_video_metadata", lambda _path: {"duration": 30, "width": 640, "height": 480}
    )
    monkeypatch.setattr(archives, "generate_thumbnail", lambda *_args: True)
    archives.archive_segment(directory, "00000000.mp4")
    archives.archive_segment(directory, "00000000.mp4")
    archive_db.db.expire_all()
    video = archive_db.db.query(Video).one()
    assert video.file_hash == hashlib.sha256(b"synthetic-evidence").hexdigest()
    assert (archives.STATIC_VIDEO_DIR / video.file_path).read_bytes() == b"synthetic-evidence"
    assert archive_db.db.query(JobExecutionLog).count() == 1
    archives.recover_segments(directory.parent)
    assert not directory.exists()


@pytest.mark.parametrize(
    ("zone", "offset"), [("Asia/Jakarta", 7), ("Asia/Makassar", 8), ("Asia/Jayapura", 9)]
)
def test_gallery_date_filters_include_local_day_and_retain_group_access(
    zone, offset, client, archive_db, monkeypatch
):
    start = datetime(2026, 11, 3, tzinfo=UTC) - timedelta(hours=offset)
    for index, instant in enumerate(
        [
            start - timedelta(seconds=1),
            start,
            start + timedelta(days=1) - timedelta(seconds=1),
            start + timedelta(days=1),
        ]
    ):
        archive_db.db.add(
            Video(
                id=f"day-{index}",
                camera_id="camera",
                camera_name="GATE-01",
                timestamp=instant,
                file_path=f"day-{index}.mp4",
                file_size=1,
                duration=30,
            )
        )
    archive_db.db.add(
        Video(
            id="outside-day",
            camera_id="outside",
            camera_name="OTHER-01",
            timestamp=start,
            file_path="outside.mp4",
            file_size=1,
            duration=30,
        )
    )
    archive_db.db.commit()
    monkeypatch.setattr(videos, "get_current_timezone", lambda _db: zone)
    monkeypatch.setattr(
        videos, "format_datetime_standard", lambda value, **_kwargs: value.isoformat()
    )
    filtered = videos._get_filtered_videos(
        archive_db.db,
        archive_db.user.group_id,
        date_from=date(2026, 11, 3),
        date_to=date(2026, 11, 3),
    )
    assert {item["id"] for item in filtered} == {"day-1", "day-2"}
    response = client.get("/video-gallery-data?date_from=2026-11-03&date_to=2026-11-03")
    assert response.status_code == 200
    assert 'data-video-id="day-1"' in response.json()["html"]
    assert "outside-day" not in response.json()["html"]
    assert (
        client.get("/video-gallery-data?date_from=2026-11-04&date_to=2026-11-03").status_code == 400
    )
    assert client.get("/video-gallery-data?date_from=2026-02-31").status_code == 422


def test_gallery_date_bounds_handle_dst_and_reject_overflow(archive_db):
    query = filter_video_dates(
        archive_db.db.query(Video), date(2026, 3, 8), date(2026, 3, 8), "America/New_York"
    )
    parameters = list(query.statement.compile().params.values())
    assert parameters == [datetime(2026, 3, 8, 5, tzinfo=UTC), datetime(2026, 3, 9, 4, tzinfo=UTC)]
    with pytest.raises(HTTPException):
        filter_video_dates(archive_db.db.query(Video), None, date.max, "UTC")


def test_failed_metadata_commit_recovers_moved_file(archive_db, monkeypatch):
    directory = (
        archives.STATIC_VIDEO_DIR / ".recording" / uuid5(NAMESPACE_URL, "camera").hex / uuid4().hex
    )
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(
        json.dumps({"camera_id": "camera", "schedule_id": "test"})
    )
    (directory / "00000000.mp4").write_bytes(b"evidence")
    monkeypatch.setattr(archives, "get_video_metadata", lambda _path: {"duration": 30})
    monkeypatch.setattr(archives, "generate_thumbnail", lambda *_args: True)
    audit = archives.log_audit
    monkeypatch.setattr(
        archives, "log_audit", Mock(side_effect=RuntimeError("Synthetic DB failure"))
    )
    with pytest.raises(RuntimeError):
        archives.archive_segment(directory, "00000000.mp4")
    assert not (directory / "00000000.mp4").exists()
    monkeypatch.setattr(archives, "log_audit", audit)
    archives.recover_segments(directory.parent)
    assert archive_db.db.query(Video).count() == 1


def test_migration_upgrade_and_downgrade_on_isolated_schema(archive_db):
    migration = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("20261103_recording_schedules")
        .module
    )
    with archive_db.engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.downgrade()
            assert "recording_schedules" not in inspect(connection).get_table_names()
            migration.upgrade()
            assert "recording_schedules" in inspect(connection).get_table_names()


def test_timezone_migration_upgrades_early_and_current_recording_schemas(archive_db):
    migration = (
        ScriptDirectory.from_config(Config("alembic.ini"))
        .get_revision("20261103_recording_timezone")
        .module
    )
    with (
        archive_db.engine.begin() as connection,
        Operations.context(MigrationContext.configure(connection)),
    ):
        migration.downgrade()
        assert "next_run_timezone" not in {
            column["name"] for column in inspect(connection).get_columns("recording_schedules")
        }
        migration.upgrade()
        migration.upgrade()
        assert "next_run_timezone" in {
            column["name"] for column in inspect(connection).get_columns("recording_schedules")
        }


def test_real_recording_session_publishes_clip_and_thumbnail(
    client, archive_db, monkeypatch, tmp_path
):
    ffmpeg = video_utils._ffmpeg_executable()
    source = tmp_path / "synthetic.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=10",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-g",
            "10",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    schedule_id = client.post(
        "/admin/recording-schedules/api", json=payload(enabled=True, clip_seconds=5)
    ).json()["id"]
    row = archive_db.db.get(RecordingSchedule, schedule_id)
    plan = archives.load_plan(archive_db.db, row)
    owner = leases.acquire_recording_lease("camera", schedule_id)
    monkeypatch.setattr(archives, "_hls_recording_url", lambda _camera: str(source))
    session = archives.RecordingSession(plan, owner)
    session.run()
    archive_db.db.expire_all()
    video = archive_db.db.query(Video).one()
    assert video.duration >= 5
    assert len(video.file_hash) == 64
    assert (archives.STATIC_VIDEO_DIR / video.file_path).with_suffix(".jpg").is_file()
    assert archive_db.db.get(CameraRecordingState, "camera").status == "idle"


def test_supervisor_finalizes_segments_on_parent_pipe_eof(tmp_path):
    ffmpeg = video_utils._ffmpeg_executable()
    if not Path(ffmpeg).is_file():
        pytest.skip("Local FFmpeg executable unavailable")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x120:rate=10",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "10",
        "-f",
        "segment",
        "-segment_time",
        "1",
        "-reset_timestamps",
        "1",
        "-segment_list",
        "pipe:1",
        "-segment_list_type",
        "csv",
        str(tmp_path / "%08d.mp4"),
    ]
    process = subprocess.Popen(
        [sys.executable, "-m", "app.jobs.recording_process"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    messages = queue.Queue()
    reader = threading.Thread(
        target=archives.RecordingSession.read_segments, args=(process, messages), daemon=True
    )
    reader.start()
    try:
        process.stdin.write(json.dumps(command) + "\n")
        process.stdin.flush()
        first = messages.get(timeout=12)
        children = psutil.Process(process.pid).children()
        assert children
        process.stdin.close()  # Simulate the worker disappearing without graceful shutdown.
        assert process.wait(timeout=18) == 0
        reader.join(timeout=3)
        assert not any(child.is_running() for child in children)
        segment = tmp_path / Path(first).name
        assert segment.stat().st_size > 0
        assert video_utils.get_video_metadata(str(segment))["duration"] > 0
    finally:
        archives.RecordingSession.stop_supervisor(process)
        process.stdout.close()
        process.stderr.close()


def test_continuous_session_finalizes_partial_segment_on_stop(
    client, archive_db, monkeypatch, tmp_path
):
    source = tmp_path / "continuous-source.mp4"
    subprocess.run(
        [
            video_utils._ffmpeg_executable(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x120:rate=10",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-g",
            "10",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    schedule_id = client.post(
        "/admin/recording-schedules/api",
        json=payload(enabled=True, mode="continuous", segment_seconds=30),
    ).json()["id"]
    row = archive_db.db.get(RecordingSchedule, schedule_id)
    plan = archives.load_plan(archive_db.db, row)
    owner = leases.acquire_recording_lease("camera", schedule_id)
    monkeypatch.setattr(archives, "_hls_recording_url", lambda _camera: str(source))
    build = archives.build_archive_command

    def realtime_source(*args):
        command = build(*args)
        index = command.index("-i")
        command[index:index] = ["-re", "-stream_loop", "-1"]
        return command

    monkeypatch.setattr(archives, "build_archive_command", realtime_source)
    session = archives.RecordingSession(plan, owner)
    stopper = threading.Timer(2, session.stop.set)
    stopper.start()
    try:
        session.run()
    finally:
        stopper.cancel()
    archive_db.db.expire_all()
    video = archive_db.db.query(Video).one()
    assert 0 < video.duration < 30
    assert archive_db.db.get(CameraRecordingState, "camera").status == "idle"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("rtsp://camera/live", "-timeout"),
        ("RTSP://camera/live", "-timeout"),
        ("https://camera/live.m3u8", "-rw_timeout"),
    ],
)
def test_archive_timeout_matches_input_protocol(source, expected, tmp_path):
    command = archives.build_archive_command(
        source, RecordingSchedulePayload(**payload()), tmp_path
    )
    assert command[command.index(expected) + 1] == "15000000"
    other = "-rw_timeout" if expected == "-timeout" else "-timeout"
    assert other not in command


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("Option rw_timeout not found.", "input_option"),
        ("method DESCRIBE failed: 401 Unauthorized", "authentication"),
        ("Connection refused", "connection"),
        ("Could not find tag for codec", "codec"),
        ("Can't mux a packet with unknown timestamp", "timestamps"),
        ("No space left on device", "storage"),
    ],
)
def test_ffmpeg_diagnostics_never_forward_stream_secrets(raw, code):
    assert recording_process.classify_ffmpeg_error(raw) == code
    codes = queue.Queue()
    secret = "rtsp://private-user:secret-password@camera/live?token=secret-token"
    recording_process.read_errors(StringIO((f"{raw} {secret}\n") * 100), codes)
    assert codes.qsize() == 1
    safe = recording_process.FFMPEG_ERRORS[codes.get_nowait()]
    assert "secret" not in safe
    assert "private-user" not in safe


@pytest.mark.parametrize(
    "body",
    [
        "not JSON secret-password",
        '{"recording_error": {"secret-password": 1}}',
        '{"recording_error":"rtsp://private:secret@camera"}',
    ],
)
def test_worker_rejects_untrusted_supervisor_error_payloads(body):
    process = SimpleNamespace(stderr=StringIO(body))
    assert (
        archives.RecordingSession.process_error(process)
        == recording_process.FFMPEG_ERRORS["unknown"]
    )


def test_rtsp_archive_reaches_local_camera_and_reports_authentication_safely(tmp_path):
    # RTSP uses a demuxer-specific timeout. A lavfi/file-only test cannot catch rw_timeout failures.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    reached = threading.Event()

    def reject_camera():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                connection.recv(8192)
                reached.set()
                connection.sendall(
                    b"RTSP/1.0 401 Unauthorized\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n"
                )
        finally:
            listener.close()

    server = threading.Thread(target=reject_camera, daemon=True)
    server.start()
    settings = RecordingSchedulePayload(**payload(clip_seconds=5))
    command = archives.build_archive_command(
        f"rtsp://private:secret@127.0.0.1:{listener.getsockname()[1]}/live", settings, tmp_path
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "app.jobs.recording_process"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        process.stdin.write(json.dumps(command) + "\n")
        process.stdin.flush()
        process.wait(timeout=10)
        error = process.stderr.read()
        server.join(timeout=6)
        assert reached.is_set()
        assert process.returncode != 0
        assert json.loads(error) == {"recording_error": "authentication"}
        assert "secret" not in error
        assert "private" not in error
    finally:
        archives.RecordingSession.stop_supervisor(process)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
