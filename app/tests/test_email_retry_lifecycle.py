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
import os
import smtplib
import socket
import tempfile
import unittest

from datetime import UTC, datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Session

from app.tests.test_email_retry_queue import APP_ROOT, EmailRetryQueueTests
from app.utils.email_delivery import EmailDeliveryResult, EmailTransportError, incident_utc


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
            ["_apply_email_retry_result", "process_email_retry_queue", "cleanup_email_retry_queue"],
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


class EmailRetryMigrationTests(unittest.TestCase):
    """Compile actual migration SQL and validate the repository revision chain."""

    def test_postgresql_migration_sql_and_single_head(self) -> None:
        """The migration follows the head and replaces the legacy active index."""
        root = APP_ROOT.parent
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        self.assertEqual(ScriptDirectory.from_config(config).get_heads(), ["20261007_email_retry"])
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
        output.truncate(0)
        output.seek(0)
        module.downgrade()
        self.assertIn("WHERE sent = false", output.getvalue())


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
