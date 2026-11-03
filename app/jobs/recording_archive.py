"""Independent archive sessions; scheduler jobs only dispatch short-lived work."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import queue
import shutil
import subprocess
import sys
import threading

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import update
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_config
from app.db.database import SessionLocal
from app.jobs.recording_process import FFMPEG_ERRORS
from app.models.camera import Camera
from app.models.job_execution_log import JobExecutionLog
from app.models.recording_schedule import CameraRecordingState, RecordingSchedule
from app.models.user import User
from app.models.video import Video
from app.schemas.recording_schedule import (
    RecordingSchedulePayload,
    as_utc,
    next_clip_run,
    window_end,
)
from app.utils.audit_logger import log_audit
from app.utils.camera_onvif import get_rtsp_url
from app.utils.recording_lease import (
    acquire_recording_lease,
    release_recording_lease,
    renew_recording_lease,
)
from app.utils.timezone_helper import get_current_timezone
from app.utils.video import (
    STATIC_VIDEO_DIR,
    _ffmpeg_executable,
    _hls_recording_url,
    _stream_input_args,
    generate_thumbnail,
    get_video_metadata,
)

logger = logging.getLogger(__name__)


class RecordingError(RuntimeError):
    """Expose only intentionally written public diagnostics, never raw stream errors."""


@dataclass(frozen=True)
class RecordingPlan:
    """Snapshot only settings required for one recording session."""

    id: str
    settings: RecordingSchedulePayload
    updated_at: datetime
    timezone: str
    next_run_at: datetime | None


def load_plan(db: Session, schedule: RecordingSchedule) -> RecordingPlan | None:
    """Recheck the creator's current admin and camera membership permissions."""
    owner = db.get(User, schedule.owner_id) if schedule.owner_id else None
    camera = (
        db.query(Camera)
        .options(selectinload(Camera.groups))
        .filter_by(id=schedule.camera_id)
        .first()
    )
    if (
        not owner
        or owner.role != "admin"
        or not camera
        or camera.status not in {"Active", "Restricted"}
    ):
        return None
    if owner.group_id is not None and not any(g.id == owner.group_id for g in camera.groups):
        return None
    settings = RecordingSchedulePayload.model_validate(
        {key: getattr(schedule, key) for key in RecordingSchedulePayload.model_fields}
    )
    return RecordingPlan(
        schedule.id,
        settings,
        as_utc(schedule.updated_at),
        get_current_timezone(db),
        schedule.next_run_at,
    )


def is_due(plan: RecordingPlan, now: datetime) -> bool:
    """Evaluate windows in configured local time and due timestamps in UTC."""
    if plan.settings.mode == "continuous":
        return True
    if plan.settings.mode == "window":
        return window_end(plan.settings, now, plan.timezone) is not None
    return plan.next_run_at is None or as_utc(plan.next_run_at) <= as_utc(now)


def refresh_plan(db: Session, schedule: RecordingSchedule, now: datetime) -> RecordingPlan | None:
    """Recalculate a cron's pending instant when the configured clock changes."""
    plan = load_plan(db, schedule)
    if (
        plan
        and plan.settings.mode == "clips"
        and plan.settings.cron
        and schedule.next_run_timezone != plan.timezone
    ):
        db.execute(
            update(RecordingSchedule)
            .where(
                RecordingSchedule.id == plan.id,
                RecordingSchedule.updated_at == plan.updated_at,
                RecordingSchedule.next_run_timezone == schedule.next_run_timezone,
            )
            .values(
                next_run_at=next_clip_run(plan.settings, now, plan.timezone),
                next_run_timezone=plan.timezone,
            )
        )
        db.commit()
        current = db.query(RecordingSchedule).filter_by(id=plan.id).first()
        return load_plan(db, current) if current else None
    return plan


def build_archive_command(
    source: str, settings: RecordingSchedulePayload, directory: Path
) -> list[str]:
    """Stream-copy video into keyframe-aligned segments without reconnecting per file."""
    # A clip must remain one file; avoid an empty tail segment at its exact -t boundary.
    segment_seconds = (
        settings.clip_seconds + 60 if settings.mode == "clips" else settings.segment_seconds
    )
    command = [
        _ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-timeout" if urlparse(source).scheme.lower() == "rtsp" else "-rw_timeout",
        "15000000",
        *_stream_input_args(source),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
    ]
    if settings.mode == "clips":
        command += ["-t", str(settings.clip_seconds)]
    return [
        *command,
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-segment_format",
        "mp4",
        "-reset_timestamps",
        "1",
        "-segment_format_options",
        "movflags=+faststart",
        "-segment_list",
        "pipe:1",
        "-segment_list_type",
        "csv",
        str(directory / "%08d.mp4"),
    ]


def claim_schedule(db: Session, plan: RecordingPlan, now: datetime) -> str | None:
    """Advance a due clip only if no administrator changed its settings meanwhile."""
    owner = acquire_recording_lease(plan.settings.camera_id, plan.id)
    if plan.settings.mode == "clips":
        advanced = db.execute(
            update(RecordingSchedule)
            .where(
                RecordingSchedule.id == plan.id,
                RecordingSchedule.updated_at == plan.updated_at,
                RecordingSchedule.next_run_at == plan.next_run_at,
            )
            .values(next_run_at=next_clip_run(plan.settings, now, plan.timezone))
        )
        db.commit()
        if not advanced.rowcount:
            if owner:
                release_recording_lease(plan.settings.camera_id, owner)
            return None
    return owner


def archive_segment(directory: Path, filename: str) -> None:
    """Recoverably publish one closed segment with its hash, thumbnail and audit entry."""
    manifest = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    # FFmpeg emits paths; accept only a numeric basename within this session.
    filename = Path(filename).name
    if len(filename) != 12 or not filename[:8].isdigit() or not filename.endswith(".mp4"):
        raise ValueError("Invalid recording segment name")
    relative = Path("archive") / directory.parent.name / f"{directory.name}_{filename}"
    destination = STATIC_VIDEO_DIR / relative
    source = directory / filename
    path = source if source.exists() else destination
    video_id = str(uuid5(NAMESPACE_URL, relative.as_posix()))
    with SessionLocal() as db:
        if db.get(Video, video_id):
            # A crash after committing metadata must not duplicate evidence or its audit.
            return
    metadata = get_video_metadata(str(path))
    if not metadata.get("duration") or not path.exists() or path.stat().st_size == 0:
        raise ValueError("Recording segment is incomplete")
    timestamp = datetime.fromtimestamp(path.stat().st_mtime, UTC) - timedelta(
        seconds=metadata["duration"]
    )
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        source.replace(destination)
    generate_thumbnail(str(destination), str(destination.with_suffix(".jpg")))
    with SessionLocal() as db:
        camera = db.get(Camera, manifest["camera_id"])
        if not camera:
            raise ValueError("Recording camera no longer exists")
        groups = sorted(camera.groups, key=lambda group: group.id)
        db.add(
            Video(
                id=video_id,
                camera_id=camera.id,
                camera_name=camera.hostname,
                camera_ip=camera.ip,
                camera_group=groups[0].name if groups else None,
                timestamp=timestamp,
                file_path=relative.as_posix(),
                file_size=destination.stat().st_size,
                duration=round(metadata["duration"]),
                resolution=f"{metadata.get('width', 0)}x{metadata.get('height', 0)}",
                file_hash=digest,
            )
        )
        db.execute(
            update(CameraRecordingState)
            .where(CameraRecordingState.camera_id == camera.id)
            .values(last_recorded_at=datetime.now(UTC), last_error=None)
        )
        now = datetime.now(UTC)
        db.add(
            JobExecutionLog(
                job_id="recording_dispatch",
                job_name="Archive Recording",
                started_at=timestamp,
                ended_at=now,
                duration_ms=int(metadata["duration"] * 1000),
                status="success",
                records_processed=1,
                metadata_json=json.dumps(
                    {
                        "schedule_id": manifest["schedule_id"],
                        "camera_id": camera.id,
                        "video_id": video_id,
                    }
                ),
            )
        )
        # log_audit commits the video, history and audit atomically.
        log_audit(
            db,
            user="recording-worker",
            action="create_video_record",
            target=camera.hostname,
            extra={
                "source": "recording_schedule",
                "schedule_id": manifest["schedule_id"],
                "video_id": video_id,
            },
        )


def recover_segments(camera_directory: Path) -> None:
    """Retry closed segments left before a crash; retain incomplete files for inspection."""
    if not camera_directory.exists():
        return
    for directory in camera_directory.iterdir():
        if not directory.is_dir() or not (directory / "session.json").is_file():
            continue
        final = STATIC_VIDEO_DIR / "archive" / camera_directory.name
        names = {path.name for path in directory.glob("*.mp4")}
        names.update(
            path.name[len(directory.name) + 1 :] for path in final.glob(f"{directory.name}_*.mp4")
        )
        for filename in sorted(names):
            try:
                archive_segment(directory, filename)
            except ValueError:
                logger.warning("Incomplete archive segment retained in session %s", directory.name)
        clean_session(directory)


def clean_session(directory: Path) -> None:
    """Remove a recovery manifest only after every moved segment has a database row."""
    if any(directory.glob("*.mp4")):
        return
    final = STATIC_VIDEO_DIR / "archive" / directory.parent.name
    with SessionLocal() as db:
        for path in final.glob(f"{directory.name}_*.mp4"):
            relative = path.relative_to(STATIC_VIDEO_DIR).as_posix()
            if not db.get(Video, str(uuid5(NAMESPACE_URL, relative))):
                return
    (directory / "session.json").unlink(missing_ok=True)
    directory.rmdir()


class RecordingSession:
    """One camera's supervised FFmpeg session and asynchronous segment finalization."""

    def __init__(self, plan: RecordingPlan, owner: str) -> None:
        self.plan = plan
        self.owner = owner
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name=f"archive-{plan.id}", daemon=True)

    def current(self) -> bool:
        """Apply edits, disablement, membership changes and timezone changes within five seconds."""
        with SessionLocal() as db:
            row = db.get(RecordingSchedule, self.plan.id)
            current = load_plan(db, row) if row and row.enabled else None
        if (
            not current
            or current.updated_at != self.plan.updated_at
            or current.timezone != self.plan.timezone
        ):
            return False
        return (
            current.settings.mode != "window"
            or window_end(current.settings, datetime.now(UTC), current.timezone) is not None
        )

    def run(self) -> None:
        """Contain session failures, fence lease loss and preserve closed segments."""
        error = None
        try:
            self.record()
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, RecordingError)
                else (
                    "Recording failed; check camera stream, available storage and FFmpeg compatibility."
                )
            )
            logger.error(
                "Archive recording failed for schedule %s (%s): %s",
                self.plan.id,
                type(exc).__name__,
                error,
            )
            with SessionLocal() as db:
                now = datetime.now(UTC)
                db.add(
                    JobExecutionLog(
                        job_id="recording_dispatch",
                        job_name="Archive Recording",
                        started_at=now,
                        ended_at=now,
                        status="fail",
                        error_message=error,
                        records_processed=0,
                        metadata_json=json.dumps(
                            {"schedule_id": self.plan.id, "camera_id": self.plan.settings.camera_id}
                        ),
                    )
                )
                db.commit()
        finally:
            release_recording_lease(self.plan.settings.camera_id, self.owner, error)

    def record(self) -> None:
        """Keep heartbeat supervision independent of potentially slow file hashing."""
        camera_id = self.plan.settings.camera_id
        key = uuid5(NAMESPACE_URL, camera_id).hex
        camera_directory = STATIC_VIDEO_DIR / ".recording" / key
        camera_directory.mkdir(parents=True, exist_ok=True)
        recover_segments(camera_directory)
        with SessionLocal() as db:
            camera = db.get(Camera, camera_id)
            source = _hls_recording_url(camera) or get_rtsp_url(camera)
        if not source:
            raise RecordingError("The camera recording stream could not be resolved.")
        if not self.current():
            return
        if not renew_recording_lease(camera_id, self.owner):
            raise RecordingError("The camera recording lease expired before stream startup.")
        directory = camera_directory / uuid4().hex
        directory.mkdir()
        (directory / "session.json").write_text(
            json.dumps({"camera_id": camera_id, "schedule_id": self.plan.id}), encoding="utf-8"
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "app.jobs.recording_process"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        messages: queue.Queue[str] = queue.Queue()
        reader = threading.Thread(target=self.read_segments, args=(process, messages), daemon=True)
        reader.start()
        futures: list[Future] = []
        try:
            process.stdin.write(
                json.dumps(build_archive_command(source, self.plan.settings, directory)) + "\n"
            )
            process.stdin.flush()
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-save") as finalizer:
                controlled = self.monitor(process, messages, directory, finalizer, futures)
                self.stop_supervisor(process)
                reader.join(timeout=5)
                while not messages.empty():
                    futures.append(
                        finalizer.submit(archive_segment, directory, messages.get_nowait())
                    )
                for future in futures:
                    future.result()
            if not controlled and (process.returncode or self.plan.settings.mode != "clips"):
                raise RecordingError(self.process_error(process))
            if not any((STATIC_VIDEO_DIR / "archive" / key).glob(f"{directory.name}_*.mp4")):
                raise RuntimeError("Recording produced no completed video")
            clean_session(directory)
        finally:
            self.stop_supervisor(process)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    @staticmethod
    def process_error(process: subprocess.Popen[str]) -> str:
        """Accept only known supervisor codes; discard unexpected output entirely."""
        try:
            code = json.loads(process.stderr.read(2048)).get("recording_error", "unknown")
        except (ValueError, AttributeError):
            code = "unknown"
        if not isinstance(code, str):
            code = "unknown"
        return FFMPEG_ERRORS.get(code, FFMPEG_ERRORS["unknown"])

    def monitor(
        self,
        process: subprocess.Popen[str],
        messages: queue.Queue[str],
        directory: Path,
        finalizer: ThreadPoolExecutor,
        futures: list[Future],
    ) -> bool:
        """Maintain leases and pipe heartbeats while a separate thread publishes segments."""
        last_segment = monotonic()
        next_check = 0.0
        seconds = (
            self.plan.settings.clip_seconds
            if self.plan.settings.mode == "clips"
            else self.plan.settings.segment_seconds
        )
        while process.poll() is None:
            if self.stop.is_set():
                return True
            if monotonic() >= next_check:
                next_check = monotonic() + 5
                if not self.current():
                    return True
                self.heartbeat(process)
            while not messages.empty():
                futures.append(finalizer.submit(archive_segment, directory, messages.get_nowait()))
                last_segment = monotonic()
            for future in futures:
                if future.done():
                    future.result()
            futures[:] = [future for future in futures if not future.done()]
            if monotonic() - last_segment > seconds + 90:
                raise RecordingError("The camera stream stopped producing video.")
            self.stop.wait(1)
        return False

    def heartbeat(self, process: subprocess.Popen[str]) -> None:
        """Stop on lease loss or exhausted storage before renewing child supervision."""
        if not renew_recording_lease(self.plan.settings.camera_id, self.owner):
            raise RecordingError("The camera recording lease was lost; recording stopped.")
        minimum = int(get_config("recording_min_free_mb", 1024)) * 1024 * 1024
        if shutil.disk_usage(STATIC_VIDEO_DIR).free < minimum:
            raise RecordingError("Recording paused: free disk reserve reached.")
        process.stdin.write("alive\n")
        process.stdin.flush()

    @staticmethod
    def read_segments(process: subprocess.Popen, messages: queue.Queue[str]) -> None:
        """Consume FFmpeg's completed-file CSV without blocking recorder heartbeats."""
        for row in csv.reader(process.stdout):
            if row:
                messages.put(row[0])

    @staticmethod
    def stop_supervisor(process: subprocess.Popen) -> None:
        """Give the supervisor time to finalize FFmpeg before escalation."""
        if process.poll() is not None:
            return
        try:
            process.stdin.write("stop\n")
            process.stdin.flush()
            process.wait(timeout=18)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            # Closing the control pipe causes the supervisor to stop its child as well.
            process.stdin.close()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class ArchiveRuntime:
    """Bound active cameras while keeping long recordings outside APScheduler executors."""

    def __init__(self) -> None:
        self.sessions: dict[str, RecordingSession] = {}
        self.lock = threading.Lock()
        self.stopping = False

    def dispatch(self) -> None:
        """Dispatch eligible schedules; continuous/window sessions precede periodic clips."""
        with self.lock:
            if self.stopping:
                return
            self.sessions = {
                key: value for key, value in self.sessions.items() if value.thread.is_alive()
            }
            capacity = int(get_config("recording_max_cameras", 4)) - len(self.sessions)
            if capacity <= 0:
                return
            STATIC_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            if (
                shutil.disk_usage(STATIC_VIDEO_DIR).free
                < int(get_config("recording_min_free_mb", 1024)) * 1024 * 1024
            ):
                return
            with SessionLocal() as db:
                schedules = (
                    db.query(RecordingSchedule)
                    .filter_by(enabled=True)
                    .order_by(RecordingSchedule.id)
                    .all()
                )
                schedules.sort(key=lambda row: row.mode == "clips")
                for schedule in schedules:
                    if capacity <= 0:
                        break
                    now = datetime.now(UTC)
                    plan = refresh_plan(db, schedule, now)
                    if not plan or not is_due(plan, now):
                        continue
                    owner = claim_schedule(db, plan, now)
                    if owner:
                        session = RecordingSession(plan, owner)
                        self.sessions[schedule.camera_id] = session
                        session.thread.start()
                        capacity -= 1

    def shutdown(self) -> None:
        """Finalize all active sessions before the scheduler process exits."""
        with self.lock:
            self.stopping = True
            sessions = list(self.sessions.values())
            for session in sessions:
                session.stop.set()
        for session in sessions:
            session.thread.join(timeout=30)


archive_runtime = ArchiveRuntime()


def dispatch_recordings() -> None:
    """Serializable scheduler entry point; registration alone starts no camera capture."""
    archive_runtime.dispatch()
