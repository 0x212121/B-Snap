"""Run with python -m unittest app.tests.test_snapshot_resilience -v.

These tests use real disposable processes and no cameras, application startup,
database or SMTP. Scheduler functions are loaded without their import-time DB setup.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.utils.camera_capture import read_frame
from app.utils.snapshot_locker import TTLFileLock
from app.utils.snapshot_process import run_camera_process, run_process
from app.utils import snapshot_process

ROOT = Path(__file__).resolve().parents[1]


def load_function(name: str, namespace: dict) -> object:
    """Load production function without importing the application's DB bootstrap."""
    tree = ast.parse((ROOT / "jobs/scheduler.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / "jobs/scheduler.py"), "exec"), namespace)
    return namespace[name]


class ProcessTests(unittest.TestCase):
    def test_timeout_cooldown_and_recovery(self) -> None:
        with patch.dict(snapshot_process._attempts, {}, clear=True), \
             patch.dict(snapshot_process._timeouts, {}, clear=True), \
             patch.object(snapshot_process, "monotonic") as clock, \
             patch.object(snapshot_process, "run_process") as runner:
            runner.return_value = {"status": "timeout"}
            for now in (100, 200, 300):
                clock.return_value = now
                self.assertEqual(run_camera_process("camera", 90)["status"], "timeout")
            clock.return_value = 400
            self.assertEqual(run_camera_process("camera", 90)["details"], "timeout_cooldown")
            self.assertEqual(runner.call_count, 3)
            clock.return_value = 1201
            runner.return_value = {"status": "success"}
            self.assertEqual(run_camera_process("camera", 90)["status"], "success")
            self.assertNotIn("camera", snapshot_process._timeouts)
            self.assertEqual(run_camera_process("camera", 90)["details"], "rate_limited")

    def test_worker_import_does_not_start_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LOG_DIR=directory, SECRET_KEY="test-only", PYTHONDONTWRITEBYTECODE="1",
                       DATABASE_URL="postgresql+psycopg2://test:test@127.0.0.1:1/test?connect_timeout=1")
            result = subprocess.run(
                [sys.executable, "-c", "import app.jobs.snapshot_worker; "
                 "import app.jobs.scheduler as s; assert not s.scheduler.running; "
                 "from unittest.mock import MagicMock; "
                 "s.engine=MagicMock(); s.scheduler=MagicMock(); "
                 "s.scheduler.get_jobs.return_value=[]; "
                 "s.get_config=lambda key,default: default; s.start_scheduler(); "
                 "calls=[c for c in s.scheduler.add_job.call_args_list "
                 "if c.kwargs.get('id')=='scheduled_snapshot']; "
                 "assert len(calls)==1 and calls[0].kwargs['max_instances']==1"],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=30,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_exit_statuses(self) -> None:
        for code, expected in [(0, "success"), (1, "error"), (2, "skipped"), (17, "error")]:
            with self.subTest(code=code):
                result = run_process([sys.executable, "-c", f"raise SystemExit({code})"], 5)
                self.assertEqual(result["status"], expected)

    def test_repeated_hangs_do_not_exhaust_workers(self) -> None:
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(run_process, [sys.executable, "-c", "import time; time.sleep(60)"], 0.2)
                    for _ in range(6)]
            jobs.append(pool.submit(run_process, [sys.executable, "-c", "pass"], 5))
            results = [job.result(timeout=10)["status"] for job in jobs]
        self.assertEqual(results, ["timeout"] * 6 + ["success"])
        self.assertLess(time.monotonic() - start, 10)

    def test_killed_owner_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.lock"
            command = [sys.executable, "-u", "-c",
                       "import sys,time; from pathlib import Path; "
                       "from app.utils.snapshot_locker import TTLFileLock; "
                       "lock=TTLFileLock(Path(sys.argv[1])); lock.__enter__(); "
                       "print('locked', flush=True); time.sleep(60)", str(path)]
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, **options) as process:
                try:
                    # A separate reader lets this test fail promptly on child startup failure.
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        line = pool.submit(process.stdout.readline).result(timeout=10)
                    self.assertEqual(line.strip(), "locked")
                    with self.assertRaises(RuntimeError):
                        with TTLFileLock(path, ttl_seconds=0):
                            pass
                finally:
                    process.kill()
                    process.wait(timeout=5)
            with TTLFileLock(path):
                pass
            self.assertTrue(path.exists())

    def test_lock_released_after_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.lock"
            with self.assertRaises(ValueError):
                with TTLFileLock(path):
                    raise ValueError("failed capture")
            with TTLFileLock(path):
                pass


class CaptureTests(unittest.TestCase):
    def test_real_ffmpeg_backend_reads_local_video(self) -> None:
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "sample.avi")
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 5, (32, 32))
            try:
                self.assertTrue(writer.isOpened())
                for _ in range(5):
                    writer.write(np.full((32, 32, 3), 120, dtype=np.uint8))
            finally:
                writer.release()
            frame = read_frame(path)
            self.assertIsNotNone(frame)
            self.assertEqual(frame.shape, (32, 32, 3))

    def test_release_on_read_failure_and_exception(self) -> None:
        for outcome in [(False, None), RuntimeError("decoder failed")]:
            cap = Mock()
            cap.isOpened.return_value = True
            if isinstance(outcome, Exception):
                cap.read.side_effect = outcome
            else:
                cap.read.return_value = outcome
            with patch("app.utils.camera_capture.open_capture", return_value=cap):
                if isinstance(outcome, Exception):
                    with self.assertRaises(RuntimeError):
                        read_frame("rtsp://example")
                else:
                    self.assertIsNone(read_frame("rtsp://example"))
            cap.release.assert_called_once()

    def test_release_when_open_fails(self) -> None:
        cap = Mock()
        cap.isOpened.return_value = False
        with patch("app.utils.camera_capture.open_capture", return_value=cap):
            self.assertIsNone(read_frame("rtsp://example"))
        cap.release.assert_called_once()


class SchedulerTests(unittest.TestCase):
    def test_timeout_does_not_block_later_batch(self) -> None:
        seen = []

        def run(camera_id: str, timeout: float) -> dict:
            seen.append(camera_id)
            return {"status": "timeout" if camera_id == "0" else "success"}

        configs = {"snapshot_concurrent_workers": 2, "snapshot_batch_size": 2,
                   "snapshot_batch_delay_seconds": 0}
        db = Mock()
        db.__enter__ = Mock(return_value=db)
        db.__exit__ = Mock(return_value=False)
        namespace = dict(
            load_active_cameras=lambda: [SimpleNamespace(id=str(i), hostname=str(i)) for i in range(5)],
            get_config=lambda key, default: configs.get(key, default),
            logger=logging.getLogger("test"), datetime=datetime, timezone=timezone,
            monotonic=time.monotonic, sleep=time.sleep, os=os,
            ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
            run_camera_process=run, SessionLocal=lambda: db, TaskTiming=Mock(),
        )
        function = load_function("scheduled_snapshot", namespace)
        result = function()
        self.assertEqual(set(seen), {"0", "1", "2", "3", "4"})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["records_processed"], 4)
        self.assertEqual(result["total_failed"], 1)
        self.assertEqual(result["timed_out"], 1)

    def test_job_log_preserves_partial_status(self) -> None:
        from functools import wraps

        db = Mock()
        model = Mock()
        namespace = dict(wraps=wraps, SessionLocal=lambda: db, JobExecutionLog=model,
                         logger=logging.getLogger("test"))
        decorator = load_function("logged_job", namespace)
        result = {"status": "partial", "records_processed": 3, "timed_out": 1}
        wrapped = decorator("scheduled_snapshot", "Scheduled Snapshot")(lambda: result)
        self.assertEqual(wrapped(), result)
        model.start_execution.return_value.complete.assert_called_once_with(
            db, status="partial", records=3, metadata=result
        )
        db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
