"""Security and lifecycle checks; helpers are mocked, never mount real storage."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.models.record_check import RecordCheckRun, RecordFolderStatus, RecordSource
from app.models.user import User
from app.routes import record_checks as routes
from app.routes.auth import get_current_user
from app.utils import encryption, record_check, record_mount as mounts
from app.utils.record_check import scan_record_source


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Use only record-check tables; unrelated models require PostgreSQL JSONB."""


@pytest.fixture(autouse=True)
def mount_policy(monkeypatch):
    monkeypatch.setenv("BSNAP_RECORD_MOUNTS_ENABLED", "true")
    monkeypatch.setenv("BSNAP_RECORD_MOUNT_ALLOWED_NETWORKS", "192.168.10.20/32,2001:db8::20/128")
    monkeypatch.delenv("BSNAP_RECORD_NFS_ALLOW_SYS", raising=False)
    monkeypatch.setattr(encryption, "ENCRYPTION_KEY", "test-only-record-mount-key")
    if not hasattr(os, "O_NOFOLLOW"):
        monkeypatch.setattr(
            os, "O_NOFOLLOW", 0, raising=False
        )  # Mocked Linux operations on Windows.


@pytest.fixture
def record_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    required = {
        Base.metadata.tables[name]
        for name in (
            "record_sources",
            "record_check_runs",
            "record_folder_checks",
            "record_folder_statuses",
            "record_folder_mappings",
            "record_status_events",
        )
    }
    pending = list(required)
    while pending:
        for fk in pending.pop().foreign_keys:
            if fk.column.table not in required:
                required.add(fk.column.table)
                pending.append(fk.column.table)
    Base.metadata.create_all(
        engine, tables=[table for table in Base.metadata.sorted_tables if table in required]
    )
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def api(record_db, monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: record_db

    def user(request: Request):
        role = request.headers.get("x-test-role")
        if not role:
            raise HTTPException(401, "Authentication required")
        return User(username="mount-admin", role=role, password="unused")

    app.dependency_overrides[get_current_user] = user
    audit = []
    monkeypatch.setattr(routes, "log_audit", lambda **values: audit.append(values))
    monkeypatch.setattr(routes, "mount_info", lambda _target: None)
    monkeypatch.setattr(mounts, "mount_info", lambda _target: None)
    monkeypatch.setattr(routes.sys, "platform", "linux")
    monkeypatch.setattr(mounts, "MOUNT_ROOT", tmp_path / "mounts")

    @contextmanager
    def lock(source_id):
        yield tmp_path

    monkeypatch.setattr(routes, "source_lock", lock)
    with TestClient(app) as client:
        client.headers.update({"x-test-role": "admin"})
        yield client, audit


def smb_payload(**changes):
    return {
        "name": "SMB recordings",
        "connection_type": "smb",
        "server": "192.168.10.20",
        "remote_path": "recordings",
        "smb_username": "readonly",
        "smb_password": "secret-test-password",
        **changes,
    }


@pytest.mark.parametrize(
    "server",
    [
        "localhost",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "192.168.10.21",
        "192.168.10.20,port=445",
    ],
)
def test_reject_unsafe_or_unlisted_servers(server):
    with pytest.raises(mounts.RecordMountError, match="allowlist"):
        mounts.validate_remote_config("smb", server, "recordings", username="reader")


@pytest.mark.parametrize(
    "share", ["../records", "records/subfolder", "records,vers=1.0", "records\npassword=x", ".."]
)
def test_reject_smb_share_option_injection(share):
    with pytest.raises(mounts.RecordMountError):
        mounts.validate_remote_config("smb", "192.168.10.20", share, username="reader")


@pytest.mark.parametrize(
    "export", ["relative", "/../etc", "/exports,rw", "/export\nrecords", "/export;id"]
)
def test_reject_nfs_path_injection(export):
    with pytest.raises(mounts.RecordMountError):
        mounts.validate_remote_config("nfs", "192.168.10.20", export)


def test_deployment_opt_in_and_nfs_downgrade_policy(monkeypatch):
    monkeypatch.setenv("BSNAP_RECORD_MOUNTS_ENABLED", "false")
    with pytest.raises(mounts.RecordMountError, match="disabled"):
        mounts.validate_remote_config("nfs", "192.168.10.20", "/records")
    monkeypatch.setenv("BSNAP_RECORD_MOUNTS_ENABLED", "true")
    with pytest.raises(mounts.RecordMountError, match="AUTH_SYS"):
        mounts.validate_remote_config("nfs", "192.168.10.20", "/records", nfs_security="sys")
    monkeypatch.setenv("BSNAP_RECORD_NFS_ALLOW_SYS", "true")
    mounts.validate_remote_config("nfs", "192.168.10.20", "/records", nfs_security="sys")


def test_uuid_and_ipv6_validation():
    mounts.validate_remote_config("nfs", "2001:db8::20", "/records")
    for source_id in ("../../etc", "-o", "bad", str(uuid4()).upper()):
        with pytest.raises(mounts.RecordMountError):
            mounts.managed_path(source_id)


def test_encryption_fail_closed_and_prefix_bypass(monkeypatch):
    password = "ENC:literal-password"
    source = RecordSource(smb_password_encrypted=mounts.encrypt_smb_password(password))
    assert source.smb_password_encrypted != password
    assert mounts._password(source) == password
    monkeypatch.setattr(encryption, "ENCRYPTION_KEY", None)
    with pytest.raises(mounts.RecordMountError, match="ENCRYPTION_KEY"):
        mounts.encrypt_smb_password("plaintext")
    with pytest.raises(mounts.RecordMountError, match="decrypt"):
        mounts._password(source)


@pytest.mark.parametrize(
    "password", ["injected\ndomain=other", " leading", "trailing ", "nul\x00", ""]
)
def test_reject_credential_file_injection(password):
    with pytest.raises(mounts.RecordMountError):
        mounts.encrypt_smb_password(password)


def test_mount_helper_timeout_is_sanitized(monkeypatch):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 30
        assert "shell" not in kwargs
        assert "ENCRYPTION_KEY" not in kwargs["env"]
        raise subprocess.CalledProcessError(1, args[0], stderr="password=secret")

    monkeypatch.setattr(mounts.subprocess, "run", fail)
    with pytest.raises(mounts.RecordMountError) as error:
        mounts._command(["/usr/bin/umount", "--", "/safe"])
    assert "secret" not in str(error.value)


def test_platform_error_is_clear(monkeypatch):
    monkeypatch.setattr(mounts.sys, "platform", "win32")
    with pytest.raises(mounts.RecordMountError, match="Linux"), mounts.source_lock(str(uuid4())):
        pass


def test_nfs_mount_is_read_only_and_not_reused_without_ownership(monkeypatch, tmp_path):
    monkeypatch.setattr(mounts, "MOUNT_ROOT", tmp_path / "mounts")
    source_id = str(uuid4())
    source = RecordSource(
        id=source_id,
        connection_type="nfs",
        server="192.168.10.20",
        remote_path="/records",
        nfs_security="krb5p",
        base_path=str(mounts.managed_path(source_id)),
    )
    info = {
        "type": "nfs4",
        "remote": "192.168.10.20:/records",
        "flags": mounts.REQUIRED_FLAGS,
        "options": {"vers=4.2", "sec=krb5p"},
    }
    current = [info]
    monkeypatch.setattr(mounts, "mount_info", lambda _target: current[0])
    with pytest.raises(mounts.RecordMountError, match="not owned"):
        mounts.ensure_mounted_locked(source, tmp_path)
    current[0] = None
    monkeypatch.setattr(
        mounts, "_private_dir", lambda path: path.mkdir(parents=True, exist_ok=True)
    )
    calls = []

    def command(arguments, pass_fds=()):
        calls.append(arguments)
        current[0] = info

    monkeypatch.setattr(mounts, "_command", command)
    mounts.ensure_mounted_locked(source, tmp_path)
    assert calls[0][0] == "/usr/sbin/mount.nfs"
    assert "sec=krb5p" in calls[0][-1]
    for flag in mounts.REQUIRED_FLAGS:
        assert flag in calls[0][-1].split(",")
    mounts.ensure_mounted_locked(source, tmp_path)
    assert len(calls) == 1
    current[0] = {**info, "flags": {"rw"}}
    with pytest.raises(mounts.RecordMountError, match="safety"):
        mounts.ensure_mounted_locked(source, tmp_path)
    with pytest.raises(mounts.RecordMountError, match="not owned"):
        mounts.unmount_locked(source_id, tmp_path)


def test_smb_credentials_not_in_command_or_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(mounts, "MOUNT_ROOT", tmp_path / "mounts")
    source_id = str(uuid4())
    source = RecordSource(
        id=source_id,
        connection_type="smb",
        server="192.168.10.20",
        remote_path="records",
        smb_username="readonly",
        smb_domain="DOMAIN",
        nfs_security="krb5p",
        smb_password_encrypted=mounts.encrypt_smb_password("test-secret"),
        base_path=str(mounts.managed_path(source_id)),
    )
    current = [None]
    monkeypatch.setattr(mounts, "mount_info", lambda _target: current[0])
    monkeypatch.setattr(
        mounts, "_private_dir", lambda path: path.mkdir(parents=True, exist_ok=True)
    )
    # Portable substitute for Linux memfd, containing only dummy test credentials.
    monkeypatch.setattr(
        mounts.os,
        "memfd_create",
        lambda *_args: os.open(tmp_path / "dummy-creds", os.O_CREAT | os.O_RDWR, 0o600),
        raising=False,
    )
    monkeypatch.setattr(mounts.os, "MFD_CLOEXEC", 1, raising=False)
    monkeypatch.setattr(mounts.os, "fchmod", lambda *_args: None, raising=False)
    descriptor = []

    def command(arguments, pass_fds=()):
        assert "test-secret" not in " ".join(arguments)
        assert "vers=3.1.1,seal" in arguments[-1]
        descriptor.extend(pass_fds)
        assert b"password=test-secret\n" in os.read(pass_fds[0], 4096)
        current[0] = {
            "type": "cifs",
            "remote": "//192.168.10.20/records",
            "flags": mounts.REQUIRED_FLAGS,
            "options": {"vers=3.1.1", "seal", "nodfs"},
        }

    monkeypatch.setattr(mounts, "_command", command)
    mounts.ensure_mounted_locked(source, tmp_path)
    assert "test-secret" not in (tmp_path / f"{source_id}.json").read_text()
    with pytest.raises(OSError, match=r"Bad file descriptor|handle is invalid"):
        os.fstat(descriptor[0])


def test_create_list_and_update_do_not_disclose_smb_password(api, record_db):
    client, audit = api
    response = client.post("/api/record-checks/sources", json=smb_payload())
    assert response.status_code == 200, response.text
    source_id = response.json()["source"]["id"]
    source = record_db.get(RecordSource, source_id)
    encrypted = source.smb_password_encrypted
    assert encrypted.startswith("ENC:")
    assert "secret-test-password" not in encrypted
    assert mounts._password(source) == "secret-test-password"
    for output in (response.text, client.get("/api/record-checks/sources").text, str(audit)):
        assert "secret-test-password" not in output
        assert encrypted not in output
    response = client.put(
        f"/api/record-checks/sources/{source_id}", json=smb_payload(smb_password=None)
    )
    assert response.status_code == 200
    assert source.smb_password_encrypted == encrypted


def test_api_rejects_csrf_and_non_admin(api):
    client, _ = api
    assert (
        client.post(
            "/api/record-checks/sources",
            json=smb_payload(),
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/record-checks/sources",
            content=json.dumps(smb_payload()),
            headers={"Content-Type": "text/plain"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/record-checks/sources", json=smb_payload(), headers={"x-test-role": "operator"}
        ).status_code
        == 403
    )
    client.headers.pop("x-test-role")
    assert client.post("/api/record-checks/sources", json=smb_payload()).status_code == 401


def test_validation_response_redacts_entire_credential_body(api):
    client, _ = api
    payload = smb_payload()
    del payload["name"]
    response = client.post("/api/record-checks/sources", json=payload)
    assert response.status_code == 422
    assert "secret-test-password" not in response.text


def test_unmount_disables_scheduler_and_is_audited(api, monkeypatch):
    client, audit = api
    source_id = client.post("/api/record-checks/sources", json=smb_payload()).json()["source"]["id"]
    calls = []
    monkeypatch.setattr(routes, "unmount_locked", lambda source_id, _state: calls.append(source_id))
    response = client.post(
        f"/api/record-checks/sources/{source_id}/unmount", headers={"X-Record-Mount-Action": "1"}
    )
    assert response.status_code == 200
    assert calls == [source_id]
    assert client.get("/api/record-checks/sources").json()["sources"][0]["enabled"] is False
    assert audit[-1]["action"] == "unmount_record_source"


def test_local_source_still_scans_without_native_mount_permissions(
    record_db, tmp_path, monkeypatch
):
    monkeypatch.setenv("BSNAP_RECORD_MOUNTS_ENABLED", "false")
    (tmp_path / "channel01").mkdir()
    source = RecordSource(name="Local", base_path=str(tmp_path))
    record_db.add(source)
    record_db.commit()
    results = scan_record_source(record_db, source)
    assert [result.folder_name for result in results] == ["channel01"]
    assert results[0].status == "healthy"


def test_complete_native_check_is_locked_through_commit(record_db, tmp_path, monkeypatch):
    channel = tmp_path / "channel01"
    channel.mkdir()
    os.utime(channel, (1, 1))
    source = RecordSource(
        name="Native",
        connection_type="nfs",
        server="192.168.10.20",
        remote_path="/records",
        base_path=str(tmp_path),
    )
    record_db.add(source)
    record_db.commit()
    events = []

    @contextmanager
    def lock(_source_id):
        events.append("lock")
        yield tmp_path
        assert record_db.query(RecordCheckRun).one().status == "success"
        events.append("unlock")

    monkeypatch.setattr(record_check, "source_lock", lock)
    monkeypatch.setattr(
        record_check, "ensure_mounted_locked", lambda _source, _state: events.append("mount")
    )
    result = record_check.run_record_source_check(record_db, source, send_notifications=False)
    assert events == ["lock", "mount", "unlock"]
    assert result["long_dead"] == 1
    assert record_db.query(RecordFolderStatus).one().status == "long_dead"


def test_failed_native_mount_records_failure_without_scanning_empty_directory(
    record_db, tmp_path, monkeypatch
):
    source = RecordSource(
        name="Unavailable native",
        connection_type="nfs",
        server="192.168.10.20",
        remote_path="/records",
        base_path=str(tmp_path),
    )
    record_db.add(source)
    record_db.commit()

    @contextmanager
    def lock(_source_id):
        yield tmp_path

    def fail(_source, _state):
        raise mounts.RecordMountError("Storage unavailable")

    monkeypatch.setattr(record_check, "source_lock", lock)
    monkeypatch.setattr(record_check, "ensure_mounted_locked", fail)
    with pytest.raises(mounts.RecordMountError, match="Storage unavailable"):
        record_check.run_record_source_check(record_db, source, send_notifications=False)
    assert record_db.query(RecordCheckRun).one().status == "fail"
    assert record_db.query(RecordFolderStatus).count() == 0


def test_busy_unmount_leaves_source_enabled(api, monkeypatch):
    client, audit = api
    source_id = client.post("/api/record-checks/sources", json=smb_payload()).json()["source"]["id"]

    def busy(_source_id, _state):
        raise mounts.RecordMountError("Source is busy")

    monkeypatch.setattr(routes, "unmount_locked", busy)
    response = client.post(
        f"/api/record-checks/sources/{source_id}/unmount", headers={"X-Record-Mount-Action": "1"}
    )
    assert response.status_code == 409
    assert client.get("/api/record-checks/sources").json()["sources"][0]["enabled"] is True
    assert audit[-1]["action"] == "unmount_record_source_failed"


def test_migration_preserves_existing_local_rows_and_downgrades():
    path = Path("alembic/versions/20261006_record_mounts.py")
    spec = importlib.util.spec_from_file_location("record_mount_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE record_sources (id TEXT PRIMARY KEY, base_path TEXT NOT NULL)")
        )
        connection.execute(text("INSERT INTO record_sources VALUES ('existing', '/mnt/records')"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        row = connection.execute(
            text("SELECT base_path, connection_type, nfs_security FROM record_sources")
        ).one()
        assert tuple(row) == ("/mnt/records", "local", "krb5p")
        migration.downgrade()
        assert {column["name"] for column in inspect(connection).get_columns("record_sources")} == {
            "id",
            "base_path",
        }
