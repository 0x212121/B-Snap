"""Isolated storage and archive checks without cameras or application data."""

from __future__ import annotations

import base64
import hashlib
import logging

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cryptography.fernet import Fernet

from app.jobs import audit_archive as archive
from app.utils import storage_monitor as storage


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override SQLite schema setup: all database operations here are mocked."""


@pytest.mark.parametrize("key", [None, "invalid-fernet-key", "invalid-\u2603-key"])
def test_invalid_archive_key_preserves_source_logs(
    key: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    if key is None:
        monkeypatch.delenv("AUDIT_ARCHIVE_KEY", raising=False)
    else:
        monkeypatch.setenv("AUDIT_ARCHIVE_KEY", key)
    db = MagicMock()
    service = archive.AuditArchiveService(db)
    service.get_logs_to_archive = MagicMock(
        return_value=[SimpleNamespace(id=1, timestamp=datetime(2020, 1, 1, tzinfo=UTC))]
    )
    service.export_to_json = MagicMock(return_value='[{"id": 1}]')
    service._copy_to_legacy = MagicMock()
    service._delete_archived_logs = MagicMock()
    with caplog.at_level(logging.WARNING), pytest.raises(ValueError, match="AUDIT_ARCHIVE_KEY"):
        service.archive_logs()
    service._copy_to_legacy.assert_not_called()
    service._delete_archived_logs.assert_not_called()
    assert list(tmp_path.iterdir()) == []
    if key is not None:
        assert key not in caplog.text
    else:
        assert "AUDIT_ARCHIVE_KEY" not in archive.os.environ


def test_archive_decrypts_after_service_recreation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Synthetic key for this test only; never load deployment keys.
    key = base64.urlsafe_b64encode(bytes(range(32)))
    monkeypatch.setenv("AUDIT_ARCHIVE_KEY", key.decode())
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    first = archive.AuditArchiveService(MagicMock())
    payload = b"isolated archive contents"
    encrypted, key_id = first._encrypt_data(payload)
    path = tmp_path / "archive.enc"
    path.write_bytes(encrypted)
    second = archive.AuditArchiveService(MagicMock())
    assert Fernet(second._get_encryption_key()).decrypt(path.read_bytes()) == payload
    assert key_id == hashlib.sha256(key).hexdigest()
    assert key.decode() not in caplog.text


def test_published_archive_stores_fingerprint_and_verifies_before_deleting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = base64.urlsafe_b64encode(bytes(range(32)))
    monkeypatch.setenv("AUDIT_ARCHIVE_KEY", key.decode())
    monkeypatch.setattr(archive, "ARCHIVE_DIR", tmp_path)
    db = MagicMock()
    service = archive.AuditArchiveService(db)
    service.get_logs_to_archive = MagicMock(
        return_value=[SimpleNamespace(id=1, timestamp=datetime(2020, 1, 1, tzinfo=UTC))]
    )
    service.export_to_json = MagicMock(return_value='[{"id": 1}]')
    service._copy_to_legacy = MagicMock(return_value=1)
    service._delete_archived_logs = MagicMock(return_value=1)
    verification = service._verify_archive_integrity

    def verify_before_database_changes(path: Path, logs: list) -> bool:
        service._copy_to_legacy.assert_not_called()
        service._delete_archived_logs.assert_not_called()
        return verification(path, logs)

    monkeypatch.setattr(service, "_verify_archive_integrity", verify_before_database_changes)
    result = service.archive_logs()
    assert result["status"] == "success"
    history = db.add.call_args.args[0]
    assert history.encryption_key_id == hashlib.sha256(key).hexdigest()
    assert Path(history.file_path).is_file()
    assert history.checksum == service._calculate_checksum(Path(history.file_path))


def test_breakdown_uses_application_paths_after_cwd_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    monkeypatch.setattr(storage, "__file__", str(root / "app" / "utils" / "storage_monitor.py"))
    monkeypatch.setattr(storage, "LOG_DIR", root / "logs")
    for name, size in (("static/snapshots", 3), ("static/videos", 5), ("logs", 7)):
        path = root / name
        path.mkdir(parents=True)
        (path / "data").write_bytes(b"x" * size)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert storage.get_storage_breakdown() == {"snapshots": 3, "videos": 5, "logs": 7, "other": 0}


@pytest.mark.parametrize("override", [None, "/isolated/media"])
def test_capacity_checks_footage_filesystem(
    override: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if override is None:
        monkeypatch.delenv("STORAGE_MONITOR_PATH", raising=False)
    else:
        monkeypatch.setenv("STORAGE_MONITOR_PATH", override)
    usage = MagicMock(return_value=(10000, 1000, 9000, 10.0))
    monkeypatch.setattr(storage, "get_disk_usage", usage)
    monkeypatch.setattr(
        storage,
        "get_storage_breakdown",
        lambda: {"snapshots": 0, "videos": 0, "logs": 0, "other": 0},
    )
    monkeypatch.setattr(storage, "calculate_growth_rate", lambda _db: 0.0)
    monkeypatch.setattr(storage, "check_storage_thresholds", lambda *_args: ("normal", None))
    storage.record_storage_metric(MagicMock())
    expected = override or str(Path(storage.__file__).resolve().parents[2] / "static" / "snapshots")
    usage.assert_called_once_with(expected)
