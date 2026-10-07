"""Regression checks for Docker privilege, credentials and administration access."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override SQLite schema setup: these checks never access a database."""


@pytest.fixture
def compose_files() -> dict:
    """Parse tracked deployment files without loading deployment secrets."""
    yaml = pytest.importorskip("yaml", reason="Compose checks need the optional PyYAML parser")
    names = (
        "docker-compose.yml",
        "docker-compose.dev.yml",
        "docker-compose-build.yml",
        "docker-compose.record-mounts.yml",
    )
    return {name: yaml.safe_load(Path(name).read_text(encoding="utf-8")) for name in names}


def test_privileges_are_limited_to_opt_in_mounts(compose_files: dict) -> None:
    for config in compose_files.values():
        for service in config["services"].values():
            assert service.get("privileged", False) is False
    services = compose_files["docker-compose.yml"]["services"]
    for name in ("migrate", "b-snap", "scheduler", "notifier"):
        assert "no-new-privileges:true" in services[name]["security_opt"]
        assert "SYS_ADMIN" not in services[name].get("cap_add", [])
    for name in ("migrate", "notifier"):
        assert services[name]["cap_drop"] == ["ALL"]
    for name in ("b-snap", "scheduler"):
        assert "NET_RAW" in services[name]["cap_add"]
    mounts = compose_files["docker-compose.record-mounts.yml"]["services"]
    assert set(mounts) == {"b-snap", "scheduler"}
    for service in mounts.values():
        assert "SYS_ADMIN" in service["cap_add"]
        assert "apparmor=bsnap-record-mounts" in service["security_opt"]


def test_credentials_and_admin_ports_are_not_embedded(compose_files: dict) -> None:
    for filename in ("docker-compose.yml", "docker-compose-build.yml"):
        services = compose_files[filename]["services"]
        postgres = services["postgres"]
        assert postgres["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
        assert postgres["environment"]["POSTGRES_USER"] == "${POSTGRES_USER:-bsnap_user}"
        assert postgres["environment"]["POSTGRES_DB"] == "${POSTGRES_DB:-bsnap_db}"
        admin = services["pgadmin"]
        assert admin["profiles"] == ["admin"]
        assert admin["environment"]["PGADMIN_DEFAULT_PASSWORD"] == "${PGADMIN_DEFAULT_PASSWORD:-}"
        for service in (postgres, admin):
            assert all(port.startswith("127.0.0.1:") for port in service["ports"])
    healthcheck = compose_files["docker-compose.yml"]["services"]["postgres"]["healthcheck"]
    assert "$$POSTGRES_USER" in healthcheck["test"][1]
    assert "$$POSTGRES_DB" in healthcheck["test"][1]
    importer = compose_files["docker-compose-build.yml"]["services"]["pgloader"]
    assert importer["environment"]["PGLOADER_TARGET_URL"] == "${PGLOADER_TARGET_URL:-}"
    assert "$${PGLOADER_TARGET_URL:?" in importer["entrypoint"][2]


def test_dev_overlay_preserves_base_startup_and_security(compose_files: dict) -> None:
    base = compose_files["docker-compose.yml"]["services"]
    dev = compose_files["docker-compose.dev.yml"]["services"]
    assert set(dev) == {"migrate", "b-snap", "scheduler", "notifier"}
    for name, override in dev.items():
        assert name in base
        assert override == {"image": "b-snap:local"}


def test_persistent_files_are_shared_at_the_same_container_paths(compose_files: dict) -> None:
    services = compose_files["docker-compose.yml"]["services"]
    for mount in (
        "./static/snapshots:/app/static/snapshots",
        "./static/videos:/app/static/videos",
        "./logs:/app/logs",
        "./archives/audit_logs:/app/archives/audit_logs",
        "shared_tmp:/tmp/shared",
    ):
        assert mount in services["b-snap"]["volumes"]
        assert mount in services["scheduler"]["volumes"]
    assert services["b-snap"]["working_dir"] == services["scheduler"]["working_dir"] == "/app"
    assert "pgadmin_data:/var/lib/pgadmin" in services["pgadmin"]["volumes"]
    assert "pgadmin_data" in compose_files["docker-compose.yml"]["volumes"]
