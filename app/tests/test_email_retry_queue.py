"""Run with python -m unittest app.tests.test_email_retry_queue -v.

Load the production helper and model without application startup or SMTP access.
Only compatible queue/log models and a minimal camera table are created in isolated SQLite.
"""

# The unittest runner avoids the application's autouse pytest database bootstrap.
# ruff: noqa: PT009

from __future__ import annotations

import ast
import logging
import unittest

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, relationship

from app.utils.email_delivery import incident_utc

APP_ROOT = Path(__file__).resolve().parents[1]


class EmailRetryQueueTests(unittest.TestCase):
    """Verify all existing callers can persist valid retry tasks."""

    def setUp(self) -> None:
        """Create isolated mappings from the actual queue model."""
        base = declarative_base()

        class Camera(base):
            __tablename__ = "cameras"
            id = Column(String, primary_key=True)
            hostname = Column(String, default="Test camera")
            ip = Column(String, default="192.0.2.1")
            asset_no = Column(String, default="")
            location = Column(String, default="Test location")
            latitude = Column(String)
            longitude = Column(String)
            notification_fail_count = Column(Integer, default=0)
            notification_suppressed_until = Column(DateTime(timezone=True))
            last_notification_at = Column(DateTime(timezone=True))

        camera_model = Camera
        self.config = Mock(side_effect=lambda _key, default: default)
        namespace = {
            "Base": base,
            "Column": Column,
            "String": String,
            "DateTime": DateTime,
            "Integer": Integer,
            "Boolean": Boolean,
            "Text": Text,
            "ForeignKey": ForeignKey,
            "CheckConstraint": CheckConstraint,
            "Index": Index,
            "text": text,
            "UniqueConstraint": UniqueConstraint,
            "relationship": relationship,
            "datetime": datetime,
            "UTC": UTC,
            "timezone": timezone,
            "timedelta": timedelta,
            "Session": Session,
            "Camera": camera_model,
            "uuid4": uuid4,
            "get_config": self.config,
            "incident_utc": incident_utc,
            "IntegrityError": IntegrityError,
            "logger": logging.getLogger(__name__),
        }
        for relative_path, node_name in (
            ("models/email_retry_queue.py", "EmailRetryQueue"),
            ("models/camera_email_notification_log.py", "CameraEmailNotificationLog"),
            ("models/camera_email_notification_log.py", "CameraEmailNotificationRecipient"),
            ("utils/email_notifier.py", "queue_email_retry"),
        ):
            path = APP_ROOT / relative_path
            tree = ast.parse(path.read_text(encoding="utf-8"))
            node = next(
                node
                for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == node_name
            )
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
        camera_model.email_logs = relationship(
            namespace["CameraEmailNotificationLog"], back_populates="camera"
        )
        self.queue_model = namespace["EmailRetryQueue"]
        self.namespace = namespace
        self.base = base
        self.enqueue = namespace["queue_email_retry"]
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.db.add(camera_model(id="camera-1"))
        self.db.commit()
        self.camera = SimpleNamespace(id="camera-1", hostname="Test camera")

    def test_existing_callers_persist_notifications(self) -> None:
        """Offline, tamper attachment and recovery calls survive a real commit."""
        before = datetime.now(UTC)
        self.enqueue(self.db, self.camera, "offline", reason="offline incident", delay_minutes=5)
        self.enqueue(
            self.db,
            self.camera,
            "tamper",
            reason="blur",
            file_path="isolated-snapshot.jpg",
            delay_minutes=5,
        )
        self.enqueue(self.db, self.camera, "recovery", delay_minutes=5)
        after = datetime.now(UTC)
        tasks = {task.type: task for task in self.db.query(self.queue_model).all()}
        self.assertEqual(set(tasks), {"offline", "tamper", "recovery"})
        self.assertEqual(tasks["offline"].reason, "offline incident")
        self.assertEqual(tasks["tamper"].file_path, "isolated-snapshot.jpg")
        self.assertIsNone(tasks["recovery"].reason)
        self.assertIsNone(tasks["recovery"].file_path)
        self.assertEqual(len({task.id for task in tasks.values()}), len(tasks))
        for task in tasks.values():
            self.assertEqual(str(UUID(task.id)), task.id)
            self.assertEqual(task.camera_id, self.camera.id)
            self.assertEqual(task.attempts, 0)
            self.assertEqual(task.max_attempts, 5)
            self.assertFalse(task.sent)
            # SQLite drops timezone information; queue timestamps are stored as UTC.
            scheduled = task.next_retry_at.replace(tzinfo=UTC)
            self.assertGreaterEqual(scheduled, before + timedelta(minutes=5))
            self.assertLessEqual(scheduled, after + timedelta(minutes=5))

    def test_configured_limit_and_positional_delay(self) -> None:
        """Keep positional delay compatibility and use the configured retry limit."""
        self.config.side_effect = None
        self.config.return_value = 2
        before = datetime.now(UTC)
        self.enqueue(self.db, self.camera, "offline", "offline incident", 12)
        task = self.db.query(self.queue_model).one()
        self.assertEqual(task.max_attempts, 2)
        self.assertGreaterEqual(
            task.next_retry_at.replace(tzinfo=UTC), before + timedelta(minutes=12)
        )
        self.config.assert_called_once_with("email_retry_max_attempts", 5)

    def test_nonpositive_limit_allows_one_retry(self) -> None:
        """Prevent nonpositive settings from creating an immediately exhausted task."""
        self.config.side_effect = None
        self.config.return_value = 0
        self.enqueue(self.db, self.camera, "recovery")
        self.assertEqual(self.db.query(self.queue_model).one().max_attempts, 1)


if __name__ == "__main__":
    unittest.main()
