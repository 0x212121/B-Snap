"""Isolated SMTP outcome, retry lifecycle and migration SQL regression tests.

Run with python -m unittest app.tests.test_email_retry_lifecycle -v. Production
functions are loaded without DB bootstrap; SMTP is mocked and only compatible
models are created in disposable SQLite. Migration DDL is compiled for PostgreSQL.
"""

# unittest avoids the application's autouse pytest schema/bootstrap.
# ruff: noqa: PT009, PT027

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import smtplib
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest

from datetime import UTC, datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock, patch

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    func,
)
from sqlalchemy.orm import Session, joinedload

from app.tests.test_email_retry_queue import APP_ROOT, EmailRetryQueueTests
from app.utils import email_retry_runtime as retry_runtime_module
from app.utils.email_delivery import EmailDeliveryResult, EmailTransportError, incident_utc
from app.utils.email_retry_runtime import (
    EmailRetryBudgetExpiredError,
    current_email_retry_runtime,
    email_retry_runtime,
    email_smtp_timeout,
)
from app.utils.email_retry_summary import EmailRetrySummary


def load_definitions(path: Path, names: list[str], namespace: dict) -> None:
    """Inject isolated dependencies while executing production function bodies."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    class RemoveAppImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
            if node.module and node.module.startswith("app."):
                return None
            return node

    nodes = []
    for name in names:
        node = next(
            n
            for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name == name
        )
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
        nodes.append(RemoveAppImports().visit(node))
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)


class EmailRetryLifecycleTests(EmailRetryQueueTests):
    """Exercise retry decisions using real incident logs and queue constraints."""

    def setUp(self) -> None:
        """Add compatible log/health tables and mock transport/configuration."""
        super().setUp()
        base = self.base

        class CameraHealth(base):
            __tablename__ = "camera_health"
            id = Column(Integer, primary_key=True)
            camera_id = Column(String, ForeignKey("cameras.id"))
            alert_cooldown_until = Column(DateTime(timezone=True))
            last_email_sent = Column(DateTime(timezone=True))
            last_alert_reason = Column(String)

        self.namespace.update(
            {
                "logger": Mock(),
                "CameraHealth": CameraHealth,
                "UniqueConstraint": UniqueConstraint,
                "EmailDeliveryResult": EmailDeliveryResult,
                "EmailTransportError": EmailTransportError,
                "EmailRetryBudgetExpiredError": EmailRetryBudgetExpiredError,
                "EmailRetrySummary": EmailRetrySummary,
                "email_retry_runtime": email_retry_runtime,
                "joinedload": joinedload,
                "monotonic": monotonic,
                "sql_func": func,
                "MAX_NOTIFICATION_FAILURES": 3,
                "NOTIFICATION_SUPPRESS_MINUTES": 60,
                "TAMPER_DEDUPLICATION_MINUTES": 15,
                "SNAPSHOT_BASE_DIR": "isolated-no-snapshot-files",
                "is_smtp_configured": Mock(return_value=True),
                "get_smtp_config": lambda: {"email_cc": None},
                "get_recipients_for_camera": Mock(return_value=["recipient@example.invalid"]),
                "_send_email_with_image": Mock(return_value=None),
                "render_template": Mock(return_value=("Test", "Test body", "Test HTML")),
                "to_current_timezone": lambda value, _db: incident_utc(value),
                "format_datetime_with_tz": lambda value: value.isoformat(),
                "SessionLocal": lambda: Session(self.engine),
            }
        )
        load_definitions(
            APP_ROOT / "utils/email_notifier.py",
            [
                "_camera_group_label",
                "_log_recipients_with_cc",
                "is_notification_suppressed",
                "record_notification_failure",
                "record_notification_success",
                "reset_notification_suppression",
                "should_send_tamper_alert",
                "_notification_gate",
                "_deliver_notification",
                "send_offline_incident_email_once",
                "send_tamper_alert",
                "send_recovery_alert",
            ],
            self.namespace,
        )
        self.namespace["_latest_notification_snapshot"] = Mock(return_value=(None, None))
        load_definitions(
            APP_ROOT / "jobs/scheduler.py",
            [
                "_apply_email_retry_result",
                "_email_retry_limit",
                "_send_queued_email",
                "_process_email_retry_task",
                "_complete_email_retry_summary",
                "process_email_retry_queue",
                "cleanup_email_retry_queue",
            ],
            self.namespace,
        )
        base.metadata.create_all(self.engine)
        self.camera = self.db.get(self.namespace["Camera"], "camera-1")
        self.incident = datetime(2026, 10, 6, 23, 59, tzinfo=UTC)
        self.transport = self.namespace["_send_email_with_image"]

    def enqueue_due(self, kind: str, **kwargs):
        """Create a due task carrying a stable incident timestamp."""
        return self.enqueue(
            self.db,
            self.camera,
            kind,
            incident_time=self.incident,
            retry_at=datetime.now(UTC) - timedelta(minutes=1),
            **kwargs,
        )

    def test_cooldown_and_suppression_do_not_spend_attempts(self) -> None:
        """Defer until policy permits delivery without connecting to SMTP."""
        until = datetime.now(UTC) + timedelta(minutes=60)
        self.camera.notification_suppressed_until = until
        self.db.commit()
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.assertEqual(task.status, "pending")
        self.assertIsNone(task.last_attempt)
        self.assertGreaterEqual(incident_utc(task.next_retry_at), until)
        self.transport.assert_not_called()
        self.camera.notification_suppressed_until = None
        task.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        task.type = "tamper"
        task.reason = "blur"
        self.db.add(
            self.namespace["CameraHealth"](
                camera_id=self.camera.id,
                alert_cooldown_until=until,
            )
        )
        self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.assertGreaterEqual(incident_utc(task.next_retry_at), until)
        self.transport.assert_not_called()

    def test_configuration_and_recipient_blocks_keep_budget(self) -> None:
        """Configuration repair can resume pending tasks without lost attempts."""
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        self.namespace["is_smtp_configured"].return_value = False
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.namespace["is_smtp_configured"].return_value = True
        self.namespace["get_recipients_for_camera"].return_value = []
        task.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.transport.assert_not_called()

    def test_failed_offline_retry_reuses_log_and_preserves_incident(self) -> None:
        """A failed incident succeeds later using its original timestamp and log."""
        self.transport.side_effect = EmailTransportError("isolated timeout")
        initial = self.namespace["send_offline_incident_email_once"](
            self.db,
            camera=self.camera,
            incident_started_at=self.incident,
            offline_duration_seconds=1800,
        )
        self.assertEqual(initial.status, "failed")
        task = self.db.query(self.queue_model).one()
        self.assertEqual(incident_utc(task.incident_time), self.incident)
        task.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        self.db.commit()
        self.transport.side_effect = None
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.status, "sent")
        self.assertEqual(task.attempts, 1)
        self.assertIsNotNone(task.last_attempt)
        self.assertIsNotNone(task.completed_at)
        log = self.db.query(self.namespace["CameraEmailNotificationLog"]).one()
        self.assertTrue(log.success)
        self.assertEqual(incident_utc(log.incident_started_at), self.incident)
        self.assertEqual(
            self.db.query(self.namespace["CameraEmailNotificationRecipient"]).count(), 1
        )
        context = self.namespace["render_template"].call_args.args[1]
        self.assertEqual(context["incident_time"], self.incident.isoformat())
        self.assertEqual(context["offline_duration"], "30")

    def test_real_smtp_failure_exhausts_task_and_releases_active_slot(self) -> None:
        """An exhausted incident stays terminal while a new incident may enqueue."""
        self.transport.side_effect = EmailTransportError("isolated timeout")
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        task.max_attempts = 1
        self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 1)
        self.assertEqual(task.status, "exhausted")
        self.assertIsNotNone(task.completed_at)
        self.assertFalse(task.sent)
        self.assertIsNone(task.next_retry_at)
        same = self.enqueue(self.db, self.camera, "offline", incident_time=self.incident)
        self.assertEqual(same.id, task.id)
        fresh = self.enqueue(
            self.db, self.camera, "offline", incident_time=self.incident + timedelta(hours=1)
        )
        self.assertNotEqual(fresh.id, task.id)
        self.assertEqual(fresh.status, "pending")

    def test_non_smtp_error_does_not_spend_attempt(self) -> None:
        """Template failures defer safely and do not consume SMTP retry budget."""
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        self.namespace["render_template"].side_effect = ValueError("isolated template error")
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.assertEqual(task.status, "pending")
        self.transport.assert_not_called()

    def test_exhausted_incident_stays_exhausted_after_queue_cleanup(self) -> None:
        """Deleting queue history must not restart the same offline incident."""
        self.transport.side_effect = EmailTransportError("isolated timeout")
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        task.max_attempts = 1
        self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        task.completed_at = datetime.now(UTC) - timedelta(days=8)
        self.db.commit()
        self.namespace["cleanup_email_retry_queue"]()
        self.assertEqual(self.db.query(self.queue_model).count(), 0)
        self.transport.reset_mock()
        result = self.namespace["send_offline_incident_email_once"](
            self.db,
            camera=self.camera,
            incident_started_at=self.incident,
            offline_duration_seconds=1800,
        )
        self.assertEqual(result.status, "exhausted")
        self.assertIsNone(
            self.enqueue(
                self.db,
                self.camera,
                "offline",
                incident_time=self.incident,
            )
        )
        self.assertEqual(self.db.query(self.queue_model).count(), 0)
        self.transport.assert_not_called()

    def test_circuit_breaker_defers_after_three_real_failures(self) -> None:
        """Circuit suppression begins at three failures and does not spend a fourth attempt."""
        self.transport.side_effect = EmailTransportError("isolated timeout")
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        for attempt in range(1, 4):
            self.namespace["process_email_retry_queue"]()
            self.db.refresh(task)
            self.assertEqual(task.attempts, attempt)
            self.assertEqual(task.status, "pending")
            self.assertGreaterEqual(
                incident_utc(task.next_retry_at),
                incident_utc(task.last_attempt) + timedelta(minutes=5 * 2 ** (attempt - 1)),
            )
            task.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
            self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.db.refresh(self.camera)
        self.assertEqual(task.attempts, 3)
        self.assertEqual(self.transport.call_count, 3)
        self.assertGreaterEqual(
            incident_utc(task.next_retry_at),
            incident_utc(self.camera.notification_suppressed_until),
        )

    def test_offset_timestamp_normalizes_without_changing_incident(self) -> None:
        """Local time near midnight retains its UTC instant and calendar conversion."""
        local = datetime.fromisoformat("2026-10-07T00:01:00+08:00")
        task = self.enqueue(self.db, self.camera, "recovery", incident_time=local)
        self.assertEqual(incident_utc(task.incident_time), datetime(2026, 10, 6, 16, 1, tzinfo=UTC))

    def test_snapshot_transitions_queue_explicit_unsent_results(self) -> None:
        """Initial tamper/recovery outcomes enqueue without needing an exception."""
        self.camera.port = 80
        self.camera.groups = []
        self.camera.group = None
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "snapshot.jpg").write_bytes(b"isolated evidence")
            for transition in ("tamper", "recovery"):
                for status in ("failed", "blocked", "deferred"):
                    with self.subTest(transition=transition, status=status):
                        tamper = transition == "tamper"
                        health = SimpleNamespace(
                            consecutive_tamper=2 if tamper else 0,
                            consecutive_normal=0 if tamper else 1,
                            tamper_status="normal" if tamper else "tampered",
                        )
                        db = Mock()
                        db.query.return_value.filter.return_value.first.return_value = self.camera
                        db.query.return_value.filter_by.return_value.first.return_value = health
                        enqueue = Mock()
                        namespace = dict(
                            self.namespace,
                            **{
                                "os": os,
                                "SNAPSHOT_BASE_DIR": directory,
                                "Snapshot": SimpleNamespace,
                                "detect_blur": Mock(return_value=(tamper, 1.0)),
                                "detect_brightness": Mock(return_value=(False, None)),
                                "detect_occlusion": Mock(return_value=(False, {"entropy": 1.0})),
                                "to_native_float": float,
                                "enforce_max_snapshots_per_camera": Mock(),
                                "set_alert_cooldown": Mock(),
                                "queue_email_retry": enqueue,
                                "TAMPER_CONFIRM_THRESHOLD": 3,
                                "RECOVERY_CONFIRM_THRESHOLD": 2,
                                "send_tamper_alert": Mock(return_value=EmailDeliveryResult(status)),
                                "send_recovery_alert": Mock(
                                    return_value=EmailDeliveryResult(status)
                                ),
                            },
                        )
                        load_definitions(
                            APP_ROOT / "utils/snapshot_utils.py",
                            ["_notify_snapshot_transition", "record_snapshot_metadata"],
                            namespace,
                        )
                        snapshot = namespace["record_snapshot_metadata"](
                            db,
                            self.camera.id,
                            "snapshot.jpg",
                            "test resolution",
                        )
                        enqueue.assert_called_once()
                        self.assertEqual(enqueue.call_args.args[2], transition)
                        self.assertEqual(
                            enqueue.call_args.kwargs["incident_time"], snapshot.timestamp
                        )

    def test_tamper_retry_does_not_duplicate_failed_incident_log(self) -> None:
        """Failed log does not suppress retry after cooldown expires."""
        self.transport.side_effect = EmailTransportError("isolated timeout")
        result = self.namespace["send_tamper_alert"](
            self.db,
            self.camera,
            "blur",
            None,
            incident_time=self.incident,
        )
        self.assertEqual(result.status, "failed")
        self.transport.side_effect = None
        result = self.namespace["send_tamper_alert"](
            self.db,
            self.camera,
            "blur",
            None,
            incident_time=self.incident,
            retry=True,
        )
        self.assertEqual(result.status, "sent")
        self.assertEqual(self.db.query(self.namespace["CameraEmailNotificationLog"]).count(), 1)
        self.assertEqual(self.transport.call_args.kwargs["snapshot_time"], self.incident)

    def test_already_sent_completes_without_spending_attempt(self) -> None:
        """A successful log finishes the task even if SMTP is now disabled."""
        self.db.add(
            self.namespace["CameraEmailNotificationLog"](
                camera_id=self.camera.id,
                incident_started_at=self.incident,
                success=True,
                reason="offline",
            )
        )
        self.db.commit()
        task = self.enqueue_due("offline")
        self.namespace["is_smtp_configured"].return_value = False
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.status, "sent")
        self.assertEqual(task.attempts, 0)
        self.transport.assert_not_called()

    def test_recovery_retry_keeps_time_and_does_not_reset_suppression(self) -> None:
        """Retry is a delivery operation, rather than a new recovery transition."""
        task = self.enqueue_due("recovery")
        self.camera.notification_fail_count = 3
        self.camera.notification_suppressed_until = datetime.now(UTC) + timedelta(hours=1)
        self.db.commit()
        self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.status, "sent")
        self.assertEqual(self.camera.notification_fail_count, 3)
        context = self.namespace["render_template"].call_args.args[1]
        self.assertEqual(context["recovery_time"], self.incident.isoformat())
        log = self.db.query(self.namespace["CameraEmailNotificationLog"]).one()
        self.assertEqual(incident_utc(log.incident_started_at), self.incident)

    def test_cleanup_uses_completion_and_preserves_pending_tasks(self) -> None:
        """First-retry successes and legacy null completion dates can be purged."""
        now = datetime.now(UTC)
        for index, (status, age, complete_age) in enumerate(
            [
                ("sent", 20, 15),
                ("exhausted", 20, 8),
                ("cancelled", 20, 8),
                ("sent", 20, 1),
                ("pending", 30, None),
                ("sent", 20, None),
            ]
        ):
            self.db.add(
                self.queue_model(
                    id=str(index),
                    camera_id=self.camera.id,
                    type=str(index),
                    incident_time=now - timedelta(days=age),
                    created_at=now - timedelta(days=age),
                    status=status,
                    sent=status == "sent",
                    attempts=0,
                    completed_at=now - timedelta(days=complete_age) if complete_age else None,
                )
            )
        self.db.commit()
        result = self.namespace["cleanup_email_retry_queue"]()
        self.assertEqual(result["records_processed"], 4)
        self.assertEqual({task.id for task in self.db.query(self.queue_model)}, {"3", "4"})

    def test_active_deduplication_keeps_original_task(self) -> None:
        """Repeated enqueue does not overwrite the original incident or attachment."""
        first = self.enqueue_due("tamper", reason="blur", file_path="first.jpg")
        duplicate = self.enqueue(
            self.db,
            self.camera,
            "tamper",
            reason="dark",
            file_path="second.jpg",
            incident_time=self.incident + timedelta(minutes=10),
        )
        self.assertEqual(duplicate.id, first.id)
        self.assertEqual(first.file_path, "first.jpg")
        self.assertEqual(incident_utc(first.incident_time), self.incident)

    def test_batch_limit_leaves_remaining_tasks_untouched(self) -> None:
        """A small configured batch never loads or updates all due tasks."""
        self.config.side_effect = lambda key, default: (
            1 if key == "email_retry_batch_size" else default
        )
        first = self.enqueue_due("offline")
        second = self.enqueue_due("recovery")
        self.namespace["_send_queued_email"] = Mock(
            return_value=EmailDeliveryResult("sent", smtp_attempted=True)
        )
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["remaining_due"], 1)
        self.assertEqual(result["stopped_reason"], "batch_limit")
        self.assertEqual(result["status"], "partial")
        self.db.refresh(first)
        self.db.refresh(second)
        self.assertEqual(first.status, "sent")
        self.assertEqual(second.status, "pending")
        self.assertEqual(second.attempts, 0)

    def test_run_budget_stops_before_next_task(self) -> None:
        """Budget exhaustion preserves untouched tasks for the next scheduled run."""
        self.enqueue_due("offline")
        remaining = self.enqueue_due("recovery")
        original_retry = remaining.next_retry_at
        self.namespace["_send_queued_email"] = Mock(
            return_value=EmailDeliveryResult("sent", smtp_attempted=True)
        )
        self.namespace["monotonic"] = Mock(side_effect=[0, 0, 121, 121])
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["stopped_reason"], "time_budget")
        self.assertEqual(result["remaining_due"], 1)
        self.db.refresh(remaining)
        self.assertEqual(remaining.attempts, 0)
        self.assertEqual(remaining.next_retry_at, original_retry)
        self.assertIsNone(current_email_retry_runtime())

    def test_eager_camera_query_and_two_commits_per_offline_task(self) -> None:
        """Camera lookup is included in the batch and counter/log writes commit together."""
        self.enqueue_due("offline", offline_duration_seconds=1800)
        statements = []
        event.listen(
            self.engine,
            "before_cursor_execute",
            lambda _conn, _cursor, sql, *_args: statements.append(sql),
        )
        session = Session(self.engine)
        original_commit = session.commit
        session.commit = Mock(wraps=original_commit)
        self.namespace["SessionLocal"] = lambda: session
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(session.commit.call_count, 2)
        camera_selects = [sql for sql in statements if "FROM cameras" in sql]
        self.assertEqual(camera_selects, [])
        self.assertTrue(any("LEFT OUTER JOIN cameras" in sql for sql in statements))

    def test_failed_and_deferred_runs_are_not_reported_as_success(self) -> None:
        """Job status distinguishes SMTP failure from a policy-only deferral."""
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        self.transport.side_effect = EmailTransportError("isolated timeout")
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["smtp_failed"], 1)
        self.assertEqual(result["records_processed"], 0)
        self.assertEqual(result["pending_total"], 1)
        self.assertIsNotNone(result["oldest_pending_age_seconds"])
        self.db.refresh(task)
        task.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        self.camera.notification_suppressed_until = datetime.now(UTC) + timedelta(hours=1)
        self.db.commit()
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["smtp_attempted"], 0)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(result["deferral_reasons"], {"notification_suppressed": 1})

    def test_processing_error_is_counted_and_empty_run_is_success(self) -> None:
        """Metadata remains informative on both processing errors and an empty queue."""
        empty = self.namespace["process_email_retry_queue"]()
        self.assertEqual(empty["status"], "success")
        self.assertEqual(empty["processed"], 0)
        self.assertIsNone(empty["oldest_pending_age_seconds"])
        self.enqueue_due("offline", offline_duration_seconds=1800)
        self.namespace["render_template"].side_effect = ValueError("synthetic-private-error")
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["processing_errors"], 1)
        self.assertNotIn("synthetic-private-error", json.dumps(result))

    def test_database_failure_closes_session(self) -> None:
        """A failure while reading the batch rolls back and releases the session."""
        session = Mock()
        session.query.side_effect = RuntimeError("isolated database error")
        self.namespace["SessionLocal"] = lambda: session
        with self.assertRaises(RuntimeError):
            self.namespace["process_email_retry_queue"]()
        session.rollback.assert_called_once()
        session.close.assert_called_once()
        self.assertIsNone(current_email_retry_runtime())

    def test_budget_deferral_does_not_spend_attempt(self) -> None:
        """An expired budget at the SMTP boundary stays a deferral."""
        task = self.enqueue_due("offline", offline_duration_seconds=1800)
        self.transport.side_effect = EmailRetryBudgetExpiredError
        result = self.namespace["process_email_retry_queue"]()
        self.db.refresh(task)
        self.assertEqual(task.attempts, 0)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["deferral_reasons"], {"run_budget_exhausted": 1})

    def test_resource_settings_are_clamped(self) -> None:
        """Misconfigured sizes/timeouts cannot create unbounded batches or waits."""
        self.config.side_effect = None
        self.config.return_value = 999999
        result = self.namespace["process_email_retry_queue"]()
        self.assertEqual(result["batch_size"], 500)
        self.assertEqual(result["max_run_seconds"], 3600)


class EmailRetryObservabilityTests(unittest.TestCase):
    """Verify metadata persistence and safe history rendering without application startup."""

    def test_wrapper_records_delivery_status_and_releases_log_transaction(self) -> None:
        """SMTP failure metadata reaches the persisted job status and error summary."""
        db = Mock()
        model = Mock()
        namespace = {
            "wraps": wraps,
            "SessionLocal": lambda: db,
            "JobExecutionLog": model,
            "logger": Mock(),
        }
        load_definitions(APP_ROOT / "jobs/scheduler.py", ["logged_job"], namespace)
        result = {
            "status": "fail",
            "records_processed": 0,
            "smtp_failed": 2,
            "error_message": "SMTP failures: 2",
        }

        def run():
            db.commit.assert_called_once()
            return result

        wrapped = namespace["logged_job"]("email_retry", "Email Retry")(run)
        self.assertEqual(wrapped(), result)
        model.start_execution.return_value.complete.assert_called_once_with(
            db,
            status="fail",
            records=0,
            metadata=result,
            error="SMTP failures: 2",
        )
        db.close.assert_called_once()

    def test_history_handles_old_and_invalid_metadata(self) -> None:
        """Old execution rows remain readable when metadata is absent or malformed."""
        namespace = {"json": json}
        load_definitions(APP_ROOT / "routes/jobs.py", ["_job_execution_metadata"], namespace)
        parse = namespace["_job_execution_metadata"]
        self.assertIsNone(parse(SimpleNamespace(metadata_json=None)))
        self.assertIsNone(parse(SimpleNamespace(metadata_json="invalid")))
        self.assertIsNone(parse(SimpleNamespace(metadata_json="[]")))
        self.assertEqual(parse(SimpleNamespace(metadata_json='{"sent":2}')), {"sent": 2})

    def test_resource_settings_form_validates_and_preserves_omitted_values(self) -> None:
        """HTTP form validation rejects unsafe budgets and old clients preserve saved values."""
        script = r"""
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional
from unittest.mock import Mock
import pytz
from fastapi import FastAPI, Form, File, Depends, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
class RemoveAppImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None if node.module and node.module.startswith('app.') else node
node = next(n for n in ast.parse(Path('app/routes/config.py').read_text(encoding='utf-8')).body
            if isinstance(n, ast.AsyncFunctionDef) and n.name == 'config_save')
node.decorator_list = []
node = RemoveAppImports().visit(node)
db = Mock()
db.query.return_value.filter_by.return_value.first.return_value = None
scope = dict(Annotated=Annotated, Optional=Optional, Form=Form, File=File, Depends=Depends, Request=Request,
             UploadFile=UploadFile, Session=object, User=object, JSONResponse=JSONResponse,
             Configuration=SimpleNamespace, pytz=pytz, clear_timezone_cache=Mock(),
             set_debug_mode=Mock(), sanitize_loggers=Mock(), get_db=lambda: db,
             admin_access_required=lambda: SimpleNamespace(role='admin'))
exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
             'isolated_config_save', 'exec'), scope)
app = FastAPI()
app.post('/config/save')(scope['config_save'])
payload = dict(snapshot_interval_minutes=10, healthcheck_interval_minutes=15,
               snapshot_concurrent_workers=2, max_screenshot_per_camera=20,
               snapshot_batch_size=10, snapshot_batch_delay_seconds=0,
               watermark_text='Test', map_title='Test', timezone='Asia/Makassar')
keys = {'email_retry_batch_size': 7, 'email_retry_max_run_seconds': 45,
        'email_retry_smtp_timeout_seconds': 4}
with TestClient(app) as client:
    assert client.post('/config/save', data={**payload, **keys}).status_code == 200
    saved = {call.args[0].key: call.args[0].value for call in db.add.call_args_list}
    assert all(saved[key] == str(value) for key, value in keys.items())
    db.reset_mock()
    assert client.post('/config/save', data=payload).status_code == 200
    touched = {call.args[0].key for call in db.add.call_args_list}
    assert not set(keys).intersection(touched)
    for key, value in [('email_retry_batch_size', 0), ('email_retry_max_run_seconds', 3601),
                       ('email_retry_smtp_timeout_seconds', 31)]:
        db.reset_mock()
        response = client.post('/config/save', data={**payload, key: value})
        assert response.status_code == 422, response.text
        db.commit.assert_not_called()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=APP_ROOT.parent,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_job_history_javascript_localizes_and_escapes(self) -> None:
        """EN/ID switching updates detail cells and untrusted data stays escaped."""
        script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('templates/jobs.html', 'utf8');
const doc = {documentElement: {lang: 'en'}};
const cell = {dataset: {retrySummary: JSON.stringify({batch_size: 50, sent: 2, deferred: 1})}};
doc.querySelectorAll = selector => selector === '[data-retry-summary]' ? [cell] : [];
let onLanguage;
const context = {document: doc, window: {addEventListener: (_name, handler) => {onLanguage = handler;}}};
vm.createContext(context);
for (const [start, end] of [
  ['function escapeAttr(', 'function getStatusBadge('],
  ['function getStatusBadge(', 'async function loadJobs('],
  ['function formatRetryDetails(', 'async function showHistory('],
]) vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
const start = source.indexOf("window.addEventListener('bsnap:languagechange'");
vm.runInContext(source.slice(start, source.indexOf('  // Initialize', start)), context);
assert(context.formatRetryDetails({batch_size: 50, sent: 2}).includes('Sent: 2'));
doc.documentElement.lang = 'id'; onLanguage();
assert(cell.textContent.includes('Terkirim: 2'));
doc.documentElement.lang = 'en'; onLanguage();
assert(cell.textContent.includes('Sent: 2'));
assert(context.getStatusBadge('<img src=x onerror=unsafe()>').includes('&lt;img'));
const malicious = {batch_size: 50, sent: '<img>', deferral_reasons: {no_recipients: '<img>'}};
assert(!context.formatRetryDetails(malicious).includes('<img>'));
assert(context.escapeAttr(JSON.stringify(malicious)).includes('&lt;img&gt;'));
assert(source.includes("headers: { Accept: 'application/json' }"));
assert(source.includes('dark:text-gray-400'));
context.BSnapDates = {locale: () => doc.documentElement.lang === 'id' ? 'id-ID' : 'en-US'};
vm.runInContext(source.slice(source.indexOf('function getDateLocale('), source.indexOf('function escapeAttr(')).replace('{{ timezone | tojson }}', '"Asia/Makassar"'), context);
assert(source.includes('data-job-datetime="${escapeAttr(h.started_at)}"'));
doc.documentElement.lang = 'en';
assert(context.formatDateTime('2026-10-07T16:30:00Z').includes('Oct 08, 2026'));
assert(context.formatDateTime('2026-10-07T16:30:00Z').includes('00:30:00'));
doc.documentElement.lang = 'id';
assert(context.formatDateTime('2026-10-07T16:30:00Z').includes('08 Okt 2026'));

"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=APP_ROOT.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class EmailRetryMigrationTests(unittest.TestCase):
    """Compile actual migration SQL and validate the repository revision chain."""

    def test_postgresql_migration_sql_and_single_head(self) -> None:
        """The migration follows the head and replaces the legacy active index."""
        root = APP_ROOT.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        self.assertEqual(
            ScriptDirectory.from_config(config).get_heads(), ["20261007_email_retry_resources"]
        )
        path = root / "alembic/versions/20261007_email_retry_lifecycle.py"
        spec = importlib.util.spec_from_file_location("isolated_email_retry_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
        module.op = Operations(context)
        module.upgrade()
        sql = output.getvalue()
        self.assertIn("ALTER COLUMN incident_time SET NOT NULL", sql)
        self.assertIn("WHERE status = 'pending'", sql)
        self.assertIn("COALESCE(last_attempt, created_at)", sql)
        self.assertIn("l.incident_started_at", sql)
        self.assertIn("DROP INDEX IF EXISTS ux_email_retry_active", sql)
        output.truncate(0)
        output.seek(0)
        module.downgrade()
        self.assertIn("WHERE sent = false", output.getvalue())
        self.assertIn("DROP INDEX IF EXISTS ux_email_retry_active", output.getvalue())

    def test_legacy_index_removal_with_present_or_missing_index(self) -> None:
        """Execute index removal on an isolated DB; other migration SQL is PostgreSQL-only."""
        path = APP_ROOT.parent / "alembic/versions/20261007_email_retry_lifecycle.py"
        spec = importlib.util.spec_from_file_location("isolated_retry_index_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for index_present in (False, True):
            for direction in ("upgrade", "downgrade"):
                with self.subTest(index_present=index_present, direction=direction):
                    engine = create_engine("sqlite://")
                    try:
                        with engine.begin() as connection:
                            connection.exec_driver_sql(
                                "CREATE TABLE email_retry_queue (camera_id INTEGER, type TEXT)"
                            )
                            if index_present:
                                connection.exec_driver_sql(
                                    "CREATE INDEX ux_email_retry_active "
                                    "ON email_retry_queue (camera_id, type)"
                                )
                            operations = Operations(MigrationContext.configure(connection))
                            stub = Mock()
                            stub.drop_index.side_effect = operations.drop_index
                            with patch.object(module, "op", stub):
                                getattr(module, direction)()
                            indexes = connection.exec_driver_sql(
                                "PRAGMA index_list('email_retry_queue')"
                            ).all()
                            self.assertEqual(indexes, [])
                            stub.create_index.assert_called_once()
                    finally:
                        engine.dispose()

    def test_due_batch_index_migration(self) -> None:
        """The new index covers pending tasks in next-retry/creation order."""
        path = APP_ROOT.parent / "alembic/versions/20261007_email_retry_resources.py"
        spec = importlib.util.spec_from_file_location("isolated_retry_resource_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "20261007_email_retry")
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
        )
        module.op = Operations(context)
        module.upgrade()
        self.assertIn("(next_retry_at, created_at) WHERE status = 'pending'", output.getvalue())
        module.downgrade()
        self.assertIn("DROP INDEX ix_email_retry_due", output.getvalue())


class EmailRetryRuntimeTests(unittest.TestCase):
    """Exercise run-scoped config caching and bounded SMTP waits."""

    def test_smtp_configuration_is_loaded_once_and_reset_between_runs(self) -> None:
        """Config changes are picked up on the next batch and credentials never reach metadata."""
        settings = {
            "smtp_host": "first.example.invalid",
            "smtp_port": "587",
            "smtp_user": "test-only",
            "smtp_pass": "isolated-password",
            "email_from": "sender@example.invalid",
            "smtp_security": "starttls",
        }
        reader = Mock(side_effect=settings.get)
        namespace = {
            "os": os,
            "get_config": reader,
            "current_email_retry_runtime": current_email_retry_runtime,
            "SMTP_SECURITY_OPTIONS": {"starttls", "ssl_tls", "none"},
        }
        load_definitions(
            APP_ROOT / "utils/smtp_config.py", ["_get_smtp_security", "get_smtp_config"], namespace
        )
        load = namespace["get_smtp_config"]
        with email_retry_runtime(monotonic() + 60, 30) as runtime:
            first = load()
            calls = reader.call_count
            settings["smtp_host"] = "second.example.invalid"
            self.assertIs(load(), first)
            self.assertEqual(reader.call_count, calls)
            self.assertEqual(first["smtp_host"], "first.example.invalid")
            self.assertNotIn("isolated-password", repr(runtime))
            result = EmailRetrySummary(50, 120).complete(
                pending_total=0,
                remaining_due=0,
                oldest_pending_age_seconds=None,
                duration_ms=0,
                stopped_reason="complete",
            )
            self.assertNotIn("isolated-password", json.dumps(result))
        self.assertIsNone(current_email_retry_runtime())
        with email_retry_runtime(monotonic() + 60, 30):
            self.assertEqual(load()["smtp_host"], "second.example.invalid")
        self.assertEqual(reader.call_count, calls * 2)

    def test_runtime_resets_on_exception_and_timeout_follows_remaining_budget(self) -> None:
        """No cached settings survive failure and expired runs cannot start SMTP."""
        with (
            patch.object(retry_runtime_module, "monotonic", return_value=100),
            self.assertRaises(ValueError),
            email_retry_runtime(110, 30),
        ):
            self.assertEqual(email_smtp_timeout(), 10)
            raise ValueError("isolated failure")
        self.assertIsNone(current_email_retry_runtime())
        self.assertEqual(email_smtp_timeout(), 30)
        with (
            patch.object(retry_runtime_module, "monotonic", return_value=111),
            email_retry_runtime(110, 30),
            self.assertRaises(EmailRetryBudgetExpiredError),
        ):
            email_smtp_timeout()

    def test_smtp_login_or_tls_failure_closes_open_connection(self) -> None:
        """An exception during setup releases the socket before propagating."""
        server = Mock()
        server.starttls.side_effect = smtplib.SMTPException("isolated TLS error")
        smtp = SimpleNamespace(SMTP=Mock(return_value=server), SMTP_SSL=Mock(return_value=server))
        namespace = {"smtplib": smtp, "ssl": ssl, "email_smtp_timeout": lambda: 2}
        load_definitions(
            APP_ROOT / "utils/email_helper.py",
            ["_set_smtp_operation_timeout", "_open_smtp_connection"],
            namespace,
        )
        config = {
            "smtp_security": "starttls",
            "smtp_host": "smtp.example.invalid",
            "smtp_port": 587,
            "has_credentials": False,
        }
        with self.assertRaises(smtplib.SMTPException):
            namespace["_open_smtp_connection"](config)
        self.assertEqual(smtp.SMTP.call_args.kwargs["timeout"], 2)
        server.sock.settimeout.assert_called_with(2)
        server.close.assert_called_once()


class EmailTransportTests(unittest.TestCase):
    """Verify only SMTP network failures carry a transport failure classification."""

    def test_timeout_is_transport_failure_and_attachment_error_is_not(self) -> None:
        """A local attachment failure must not spend an SMTP retry attempt."""
        connection = Mock(side_effect=TimeoutError("isolated timeout"))
        namespace = {
            "os": os,
            "socket": socket,
            "smtplib": smtplib,
            "datetime": datetime,
            "timezone": timezone,
            "MIMEApplication": MIMEApplication,
            "MIMEMultipart": MIMEMultipart,
            "MIMEText": MIMEText,
            "EmailTransportError": EmailTransportError,
            "_open_smtp_connection": connection,
            "get_smtp_config": lambda: {
                "is_configured": True,
                "smtp_host": "smtp.example.invalid",
                "email_from": "sender@example.invalid",
                "email_cc": None,
            },
        }
        load_definitions(APP_ROOT / "utils/email_helper.py", ["_send_email_with_image"], namespace)
        with self.assertRaises(EmailTransportError):
            namespace["_send_email_with_image"](
                ["recipient@example.invalid"], "Subject", "Group", "Camera", None, "Body", "HTML"
            )
        self.assertEqual(connection.call_count, 1)
        connection.reset_mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(OSError):
            namespace["_send_email_with_image"](
                ["recipient@example.invalid"],
                "Subject",
                "Group",
                "Camera",
                datetime.now(UTC),
                "Body",
                "HTML",
                image_path=directory,
            )
        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
