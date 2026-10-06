"""Restricted, opt-in Linux mounts for recording metadata checks.

Only deployment-allowlisted IPs, generated UUID mountpoints, and fixed read-only
options are accepted. Mount ownership is recorded per Linux mount namespace.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from app.models.record_check import RecordSource
from app.utils.encryption import decrypt_value, encrypt_value

MOUNT_ROOT = Path("/var/lib/bsnap/record-mounts")
STATE_ROOT = Path("/run/bsnap-record-mounts")
REQUIRED_FLAGS = {"ro", "nosuid", "nodev", "noexec"}
logger = logging.getLogger("record_mount")


class RecordMountError(ValueError):
    """Safe error suitable for API responses; never contains credentials."""


def managed_path(source_id: str) -> Path:
    """Return the generated mountpoint for a canonical source UUID."""
    try:
        if str(UUID(source_id)) != source_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecordMountError("Invalid record source ID") from exc
    return MOUNT_ROOT / source_id


def validate_remote_config(
    connection_type: str,
    server: str,
    remote_path: str,
    *,
    username: str = "",
    domain: str = "",
    nfs_security: str = "krb5p",
) -> None:
    """Reject option injection and destinations outside the deployment allowlist."""
    if os.getenv("BSNAP_RECORD_MOUNTS_ENABLED", "false").lower() != "true":
        raise RecordMountError("Native record mounts are disabled by deployment configuration")
    if connection_type not in {"smb", "nfs"}:
        raise RecordMountError("Unsupported record connection type")
    _validate_server(server)
    if connection_type == "smb":
        _validate_smb_share(remote_path, username, domain)
    else:
        _validate_nfs_export(remote_path, nfs_security)


def _validate_server(server: str) -> None:
    try:
        address = ipaddress.ip_address(server)
        # Literal IPs avoid DNS rebinding. Disallow local, multicast and link-local services.
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError
        networks = [
            ipaddress.ip_network(item.strip(), strict=True)
            for item in os.getenv("BSNAP_RECORD_MOUNT_ALLOWED_NETWORKS", "").split(",")
            if item.strip()
        ]
        if not networks or not any(address in network for network in networks):
            raise ValueError
    except ValueError as exc:
        raise RecordMountError(
            "Storage server must be a literal IP in the deployment allowlist"
        ) from exc
    if str(address) != server:
        raise RecordMountError("Use the canonical storage IP address")


def _validate_smb_share(remote_path: str, username: str, domain: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_$.-]{1,255}", remote_path) or remote_path in {".", ".."}:
        raise RecordMountError("SMB share must be a single share name, without a path or options")
    if not username or len(username) > 255 or len(domain) > 255:
        raise RecordMountError("SMB username is required (maximum 255 characters)")
    if (
        any(char in username + domain for char in "\r\n\x00")
        or username != username.strip()
        or domain != domain.strip()
    ):
        raise RecordMountError("Invalid SMB username or domain")


def _validate_nfs_export(remote_path: str, nfs_security: str) -> None:
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,1023}", remote_path) or ".." in remote_path.split("/"):
        raise RecordMountError("NFS export must be an absolute path without traversal or options")
    if nfs_security not in {"krb5p", "sys"}:
        raise RecordMountError("Unsupported NFS security mode")
    if nfs_security == "sys" and os.getenv("BSNAP_RECORD_NFS_ALLOW_SYS", "false").lower() != "true":
        raise RecordMountError("NFS AUTH_SYS requires explicit deployment opt-in; use krb5p")


def encrypt_smb_password(password: str) -> str:
    """Encrypt validated SMB credentials, refusing plaintext fallback."""
    if (
        not password
        or len(password) > 1024
        or any(char in password for char in "\r\n\x00")
        or password != password.strip()
    ):
        raise RecordMountError(
            "SMB password is required and must not contain newlines or edge whitespace"
        )
    try:
        # Wrapping prevents an ENC:-prefixed plaintext from bypassing encryption.
        return encrypt_value(json.dumps({"password": password}))
    except Exception as exc:
        raise RecordMountError("SMB credentials require a valid ENCRYPTION_KEY") from exc


def _password(source: RecordSource) -> str:
    try:
        encrypted = source.smb_password_encrypted
        if not encrypted or not encrypted.startswith("ENC:"):
            raise ValueError
        return json.loads(decrypt_value(encrypted))["password"]
    except Exception as exc:
        raise RecordMountError("Unable to decrypt SMB credentials; check ENCRYPTION_KEY") from exc


def _private_dir(path: Path) -> None:
    # Never follow a symlink in mount or state directory ancestors.
    for part in reversed((path, *path.parents)):
        if part.is_symlink():
            raise RecordMountError("Mount directories must not contain symlinks")
        if part.exists():
            info = part.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise RecordMountError(
                    "Mount directory ancestors must be service-owned and not writable by other users"
                )
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RecordMountError("Mount directories must be private and owned by the mount service")


def _runtime() -> Path:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RecordMountError(
            "Native mounts require Linux and mount privileges; use a local path on Windows"
        )
    _private_dir(MOUNT_ROOT)
    _private_dir(STATE_ROOT)
    namespace = os.stat("/proc/self/ns/mnt").st_ino
    state = STATE_ROOT / str(namespace)
    _private_dir(state)
    return state


@contextmanager
def source_lock(source_id: str) -> Iterator[Path]:
    """Serialize checks/mount/configuration changes across web workers."""
    managed_path(source_id)
    state = _runtime()
    import fcntl  # noqa: PLC0415 -- Linux-only module; runtime guard must run first.

    descriptor = os.open(state / f"{source_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RecordMountError(
                        "This source is busy; try again after its check completes"
                    ) from None
                time.sleep(0.05)
        yield state
    finally:
        os.close(descriptor)


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def mount_info(target: Path) -> dict | None:
    """Read the kernel mount table without touching potentially unavailable storage."""
    if sys.platform != "linux":
        return None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        left, right = line.split(" - ", 1)
        fields, filesystem = left.split(), right.split()
        if _unescape(fields[4]) == str(target):
            return {
                "type": filesystem[0],
                "remote": _unescape(filesystem[1]),
                "flags": set(fields[5].split(",")),
                "options": set(filesystem[2].split(",")),
            }
    return None


def _spec(source: RecordSource) -> dict:
    server = f"[{source.server}]" if ":" in source.server else source.server
    remote = (
        f"//{server}/{source.remote_path}"
        if source.connection_type == "smb"
        else f"{server}:{source.remote_path}"
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                source.connection_type,
                source.server,
                source.remote_path,
                source.smb_username,
                source.smb_domain,
                source.smb_password_encrypted,
                source.nfs_security,
            ]
        ).encode()
    ).hexdigest()
    return {
        "type": "cifs" if source.connection_type == "smb" else "nfs4",
        "remote": remote,
        "fingerprint": fingerprint,
        "security_options": (
            ["vers=3.1.1", "seal", "nodfs"]
            if source.connection_type == "smb"
            else ["vers=4.2", f"sec={source.nfs_security}"]
        ),
    }


def _matches(info: dict, spec: dict) -> bool:
    return (
        info["type"] == spec["type"]
        and info["remote"] == spec["remote"]
        and info["flags"] >= REQUIRED_FLAGS
        and set(spec["security_options"]) <= info["options"]
    )


def _command(arguments: list[str], pass_fds: tuple[int, ...] = ()) -> None:
    try:
        subprocess.run(
            arguments,
            check=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=pass_fds,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Helper output can contain usernames/passwords; never return or log it.
        raise RecordMountError(
            "Mount operation failed or timed out; check storage connectivity, permissions and deployment prerequisites"
        ) from exc


def unmount_locked(source_id: str, state: Path) -> None:
    """Detach only a recorded B-Snap mount while the caller holds its lock."""
    target = managed_path(source_id)
    info = mount_info(target)
    marker = state / f"{source_id}.json"
    if info:
        if (
            marker.is_symlink()
            or not marker.exists()
            or not _matches(info, json.loads(marker.read_text()))
        ):
            raise RecordMountError("Refusing to unmount a filesystem not owned by B-Snap")
        _command(["/usr/bin/umount", "--", str(target)])
        if mount_info(target):
            raise RecordMountError("Filesystem is still mounted")
    marker.unlink(missing_ok=True)


def ensure_mounted_locked(source: RecordSource, state: Path) -> Path:
    """Mount or reuse a verified source while the caller holds its lock."""
    validate_remote_config(
        source.connection_type,
        source.server or "",
        source.remote_path or "",
        username=source.smb_username or "",
        domain=source.smb_domain or "",
        nfs_security=source.nfs_security,
    )
    target = managed_path(source.id)
    if str(target) != source.base_path:
        raise RecordMountError("Managed source mountpoint does not match its generated path")
    spec = _spec(source)
    marker = state / f"{source.id}.json"
    info = mount_info(target)
    if info:
        if marker.is_symlink() or not marker.exists():
            raise RecordMountError("Refusing to reuse a filesystem not owned by B-Snap")
        previous = json.loads(marker.read_text())
        if not _matches(info, previous):
            raise RecordMountError("Mounted filesystem identity or safety flags do not match")
        if previous == spec:
            return target
        unmount_locked(source.id, state)
    _private_dir(target)
    if any(target.iterdir()):
        raise RecordMountError("Refusing to mount over a non-empty directory")
    temporary_marker = state / f"{source.id}.json.tmp"
    descriptor = os.open(
        temporary_marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as file:
        json.dump(spec, file)
    os.replace(temporary_marker, marker)
    _mount_remote(source, target, spec)
    info = mount_info(target)
    if not info or not _matches(info, spec):
        if info and info["type"] == spec["type"] and info["remote"] == spec["remote"]:
            # The helper just mounted this source, but its result is unsafe: detach it.
            _command(["/usr/bin/umount", "--", str(target)])
            if not mount_info(target):
                marker.unlink(missing_ok=True)
        raise RecordMountError(
            "Mounted filesystem did not pass identity and read-only safety checks"
        )
    return target


def _mount_remote(source: RecordSource, target: Path, spec: dict) -> None:
    options = "ro,nosuid,nodev,noexec"
    if source.connection_type == "smb":
        # Anonymous RAM-backed credentials: no password in argv, env or disk files.
        credentials = os.memfd_create("bsnap-smb", os.MFD_CLOEXEC)
        try:
            os.fchmod(credentials, 0o600)
            os.write(
                credentials,
                f"username={source.smb_username}\npassword={_password(source)}\ndomain={source.smb_domain or ''}\n".encode(),
            )
            os.lseek(credentials, 0, os.SEEK_SET)
            _command(
                [
                    "/usr/sbin/mount.cifs",
                    spec["remote"],
                    str(target),
                    "-o",
                    f"{options},vers=3.1.1,seal,nodfs,sec=ntlmssp,port=445,nosharesock,soft,echo_interval=5,credentials=/proc/self/fd/{credentials}",
                ],
                (credentials,),
            )
        finally:
            os.close(credentials)
    else:
        _command(
            [
                "/usr/sbin/mount.nfs",
                spec["remote"],
                str(target),
                "-o",
                f"{options},vers=4.2,proto=tcp,sec={source.nfs_security},soft,timeo=50,retrans=2,retry=0",
            ]
        )


@contextmanager
def record_source_path(source: RecordSource) -> Iterator[Path]:
    """Keep managed storage mounted and locked for the duration of a scan."""
    if (source.connection_type or "local") == "local":
        yield Path(source.base_path)
    else:
        with source_lock(source.id) as state:
            yield ensure_mounted_locked(source, state)


def reconcile_mounts(active_ids: set[str]) -> None:
    """Detach disabled/deleted sources in this service's own mount namespace."""
    if sys.platform != "linux" or not STATE_ROOT.exists():
        return
    state = _runtime()
    for marker in state.glob("*.json"):
        if marker.stem not in active_ids:
            try:
                with source_lock(marker.stem) as locked_state:
                    unmount_locked(marker.stem, locked_state)
            except (RecordMountError, OSError, ValueError):
                logger.warning(
                    "Record mount cleanup deferred; mount is busy or ownership could not be verified"
                )
