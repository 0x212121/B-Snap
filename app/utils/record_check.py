from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.camera import Camera
from app.models.record_check import (
    RecordCheckRun,
    RecordFolderCheck,
    RecordFolderMapping,
    RecordFolderStatus,
    RecordSource,
    RecordStatusEvent,
)
from app.utils.wa_gateway import WAGatewayService, format_phone_number

logger = logging.getLogger("record_check")

VALID_RECORD_STATUSES = {"healthy", "stale", "long_dead", "unknown", "missing"}


@dataclass(frozen=True)
class FolderScanResult:
    folder_name: str
    folder_path: str
    last_mtime: datetime | None
    age_seconds: int | None
    status: str
    camera_id: str | None = None


@dataclass(frozen=True)
class RecordNotificationItem:
    folder_name: str
    camera_name: str | None = None


def validate_record_source_path(base_path: str) -> Path:
    """Validate a configured record source path without requiring it to exist yet."""
    if not base_path or not base_path.strip():
        raise ValueError("Base path is required")

    raw_path = Path(base_path.strip())
    if not raw_path.is_absolute():
        raise ValueError("Base path must be an absolute path")

    parts = [part for part in raw_path.parts if part not in (raw_path.anchor, os.sep)]
    if any(part == ".." for part in parts):
        raise ValueError("Base path must not contain traversal segments")

    return raw_path


def classify_record_folder(age_seconds: int | None, stale_threshold: int, long_dead_threshold: int) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds >= long_dead_threshold:
        return "long_dead"
    if age_seconds > stale_threshold:
        return "stale"
    return "healthy"


def _camera_map_by_hostname(db: Session) -> dict[str, str]:
    rows = db.query(Camera.id, Camera.hostname).all()
    return {hostname.strip().lower(): camera_id for camera_id, hostname in rows if hostname}


def _manual_camera_map(db: Session, source_id: str) -> dict[str, str | None]:
    rows = (
        db.query(RecordFolderMapping.folder_name, RecordFolderMapping.camera_id)
        .filter(RecordFolderMapping.source_id == source_id)
        .all()
    )
    return {folder_name.strip().lower(): camera_id for folder_name, camera_id in rows if folder_name}


def scan_record_source(db: Session, source: RecordSource, checked_at: datetime | None = None) -> list[FolderScanResult]:
    checked_at = checked_at or datetime.now(timezone.utc)
    base_path = validate_record_source_path(source.base_path)

    if not base_path.exists():
        raise FileNotFoundError(f"Record source path does not exist: {base_path}")
    if not base_path.is_dir():
        raise NotADirectoryError(f"Record source path is not a directory: {base_path}")

    camera_map = _camera_map_by_hostname(db)
    manual_map = _manual_camera_map(db, source.id)
    results: list[FolderScanResult] = []

    with os.scandir(base_path) as entries:
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue

                stat_result = entry.stat(follow_symlinks=False)
                mtime = datetime.fromtimestamp(stat_result.st_mtime, timezone.utc)
                age_seconds = max(0, int((checked_at - mtime).total_seconds()))
                status = classify_record_folder(
                    age_seconds,
                    int(source.stale_threshold_seconds),
                    int(source.long_dead_threshold_seconds),
                )
                folder_name = entry.name
                results.append(
                    FolderScanResult(
                        folder_name=folder_name,
                        folder_path=str(Path(entry.path).resolve()),
                        last_mtime=mtime,
                        age_seconds=age_seconds,
                        status=status,
                        camera_id=manual_map.get(folder_name.strip().lower(), camera_map.get(folder_name.strip().lower())),
                    )
                )
            except OSError as exc:
                logger.warning("Could not stat record folder %s: %s", entry.path, exc)
                folder_name = entry.name
                results.append(
                    FolderScanResult(
                        folder_name=folder_name,
                        folder_path=str(Path(entry.path).absolute()),
                        last_mtime=None,
                        age_seconds=None,
                        status="unknown",
                        camera_id=manual_map.get(folder_name.strip().lower(), camera_map.get(folder_name.strip().lower())),
                    )
                )

    return sorted(results, key=lambda item: item.folder_name.lower())


def _format_wita(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    try:
        import pytz

        tz = pytz.timezone("Asia/Makassar")
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return dt.isoformat()


def _format_notification_item(item: RecordNotificationItem) -> str:
    camera_name = item.camera_name or "Unmapped"
    return f"{item.folder_name} - {camera_name}"


def _send_record_notification(
    db: Session,
    source: RecordSource,
    event_type: str,
    folders: list[RecordNotificationItem],
    run: RecordCheckRun,
) -> tuple[bool, str | None]:
    service = WAGatewayService(db)
    if not service.config.is_configured():
        return False, "GoWA not configured"

    receiver = service.config.default_receiver
    if not receiver:
        return False, "GoWA default receiver not configured"

    title = "B-SNAP Record Problem" if event_type == "record_alert" else "B-SNAP Record Recovery"
    marker = "ALERT" if event_type == "record_alert" else "RECOVERY"
    status_line = "Recording folder stopped updating" if event_type == "record_alert" else "Recording folder recovered"
    listed = "\n".join(f"- {_format_notification_item(folder)}" for folder in folders[:30])
    if len(folders) > 30:
        listed += f"\n- ... and {len(folders) - 30} more"

    remaining_problem_block = ""
    if event_type == "record_recovery":
        remaining_problem_rows = (
            db.query(RecordFolderStatus.folder_name, Camera.hostname, RecordFolderStatus.status)
            .join(Camera, RecordFolderStatus.camera_id == Camera.id, isouter=True)
            .filter(
                RecordFolderStatus.source_id == source.id,
                RecordFolderStatus.status.in_(["stale", "missing"]),
            )
            .order_by(RecordFolderStatus.status.asc(), RecordFolderStatus.folder_name.asc())
            .limit(30)
            .all()
        )
        if remaining_problem_rows:
            remaining_lines = [
                f"- {folder_name} - {camera_name or 'Unmapped'} ({status})"
                for folder_name, camera_name, status in remaining_problem_rows
            ]
            remaining_problem_block = (
                "\n\n"
                f"Still needs attention ({len(remaining_problem_rows)} channel):\n"
                + "\n".join(remaining_lines)
            )

    message = (
        f"{marker} *{title}*\n"
        f"Source: {source.name}\n"
        f"Checked: {_format_wita(run.started_at)}\n\n"
        f"{status_line} ({len(folders)} channel):\n"
        f"{listed}"
        f"{remaining_problem_block}\n\n"
        f"Healthy: {run.healthy_count} | Stale: {run.stale_count} | "
        f"Long dead: {run.long_dead_count} | Total: {run.total_folders}"
    )

    errors = []
    sent = 0
    for phone in [p.strip() for p in receiver.split(",") if p.strip()]:
        result = service.send_text(format_phone_number(phone), message)
        if result.get("success"):
            sent += 1
        else:
            errors.append(str(result.get("error", "unknown error")))

    if sent:
        return True, "; ".join(errors) if errors else None
    return False, "; ".join(errors) if errors else "No receivers sent"


def _record_event(
    db: Session,
    source: RecordSource,
    status_row: RecordFolderStatus,
    previous_status: str | None,
    event_type: str,
    message: str,
) -> RecordStatusEvent:
    event = RecordStatusEvent(
        source_id=source.id,
        folder_status_id=status_row.id,
        camera_id=status_row.camera_id,
        folder_name=status_row.folder_name,
        previous_status=previous_status,
        new_status=status_row.status,
        event_type=event_type,
        message=message,
    )
    db.add(event)
    return event


def run_record_source_check(db: Session, source: RecordSource, send_notifications: bool = True) -> dict:
    started_at = datetime.now(timezone.utc)
    run = RecordCheckRun(source_id=source.id, started_at=started_at, status="running")
    db.add(run)
    db.flush()

    try:
        results = scan_record_source(db, source, checked_at=started_at)
        alert_events: list[RecordStatusEvent] = []
        recovery_events: list[RecordStatusEvent] = []
        alert_folders: list[RecordNotificationItem] = []
        recovery_folders: list[RecordNotificationItem] = []
        camera_names = {
            camera_id: hostname
            for camera_id, hostname in db.query(Camera.id, Camera.hostname).all()
            if camera_id and hostname
        }

        counts = {"healthy": 0, "stale": 0, "long_dead": 0, "unknown": 0, "missing": 0}
        seen_folder_keys: set[str] = set()
        for result in results:
            seen_folder_keys.add(result.folder_name.strip().lower())
            counts[result.status] = counts.get(result.status, 0) + 1

            db.add(
                RecordFolderCheck(
                    run_id=run.id,
                    source_id=source.id,
                    camera_id=result.camera_id,
                    folder_name=result.folder_name,
                    folder_path=result.folder_path,
                    last_mtime=result.last_mtime,
                    age_seconds=result.age_seconds,
                    status=result.status,
                    checked_at=started_at,
                )
            )

            status_row = (
                db.query(RecordFolderStatus)
                .filter(
                    RecordFolderStatus.source_id == source.id,
                    func.lower(RecordFolderStatus.folder_name) == result.folder_name.lower(),
                )
                .first()
            )
            previous_status = status_row.status if status_row else None
            previous_alert_active = bool(status_row.alert_active) if status_row else False

            if not status_row:
                status_row = RecordFolderStatus(
                    source_id=source.id,
                    folder_name=result.folder_name,
                    folder_path=result.folder_path,
                    status=result.status,
                    status_changed_at=started_at,
                    last_checked_at=started_at,
                )
                db.add(status_row)
                db.flush()

            status_changed = previous_status != result.status
            status_row.camera_id = result.camera_id
            status_row.folder_name = result.folder_name
            status_row.folder_path = result.folder_path
            status_row.last_mtime = result.last_mtime
            status_row.age_seconds = result.age_seconds
            status_row.status = result.status
            status_row.last_checked_at = started_at
            if status_changed:
                status_row.status_changed_at = started_at

            notification_item = RecordNotificationItem(
                folder_name=result.folder_name,
                camera_name=camera_names.get(result.camera_id),
            )

            if result.status == "stale" and not previous_alert_active:
                status_row.alert_active = True
                status_row.last_alert_sent_at = started_at
                alert_folders.append(notification_item)
                alert_events.append(
                    _record_event(
                        db,
                        source,
                        status_row,
                        previous_status,
                        "record_alert",
                        f"Folder not updated for {result.age_seconds} seconds",
                    )
                )
            elif result.status == "stale" and previous_alert_active:
                successful_alert = (
                    db.query(RecordStatusEvent)
                    .filter(
                        RecordStatusEvent.folder_status_id == status_row.id,
                        RecordStatusEvent.event_type.in_(["record_alert", "record_alert_retry"]),
                        RecordStatusEvent.notification_sent.is_(True),
                        RecordStatusEvent.created_at >= status_row.status_changed_at,
                    )
                    .first()
                )
                retry_due = (
                    status_row.last_alert_sent_at is None
                    or status_row.last_alert_sent_at <= started_at - timedelta(minutes=30)
                )
                if not successful_alert and retry_due:
                    status_row.last_alert_sent_at = started_at
                    alert_folders.append(notification_item)
                    alert_events.append(
                        _record_event(
                            db,
                            source,
                            status_row,
                            previous_status,
                            "record_alert_retry",
                            f"Retrying stale folder alert after previous notification failure; age={result.age_seconds} seconds",
                        )
                    )
            elif result.status == "healthy" and previous_alert_active:
                status_row.alert_active = False
                status_row.last_recovery_sent_at = started_at
                recovery_folders.append(notification_item)
                recovery_events.append(
                    _record_event(
                        db,
                        source,
                        status_row,
                        previous_status,
                        "record_recovery",
                        "Folder update returned to healthy threshold",
                    )
                )
            elif status_changed:
                _record_event(
                    db,
                    source,
                    status_row,
                    previous_status,
                    "status_change",
                    f"Record folder status changed to {result.status}",
                )

        existing_statuses = (
            db.query(RecordFolderStatus)
            .filter(RecordFolderStatus.source_id == source.id)
            .all()
        )
        for status_row in existing_statuses:
            folder_key = status_row.folder_name.strip().lower()
            if folder_key in seen_folder_keys or status_row.status == "missing":
                continue

            previous_status = status_row.status
            previous_alert_active = bool(status_row.alert_active)
            status_row.status = "missing"
            status_row.age_seconds = None
            status_row.last_checked_at = started_at
            status_row.status_changed_at = started_at
            counts["missing"] += 1

            notification_item = RecordNotificationItem(
                folder_name=status_row.folder_name,
                camera_name=camera_names.get(status_row.camera_id),
            )
            event_type = "folder_missing"
            if not previous_alert_active:
                status_row.alert_active = True
                status_row.last_alert_sent_at = started_at
                alert_folders.append(notification_item)
                event_type = "record_alert"

            alert_events.append(
                _record_event(
                    db,
                    source,
                    status_row,
                    previous_status,
                    event_type,
                    "Record folder disappeared from source path",
                )
            )

        run.total_folders = len(results)
        run.healthy_count = counts["healthy"]
        run.stale_count = counts["stale"]
        run.long_dead_count = counts["long_dead"]
        run.unknown_count = counts["unknown"] + counts["missing"]
        run.status = "success"
        run.ended_at = datetime.now(timezone.utc)
        db.flush()

        if send_notifications and alert_folders:
            sent, error = _send_record_notification(db, source, "record_alert", alert_folders, run)
            for event in alert_events:
                event.notification_sent = sent
                event.notification_error = error

        if send_notifications and recovery_folders:
            sent, error = _send_record_notification(db, source, "record_recovery", recovery_folders, run)
            for event in recovery_events:
                event.notification_sent = sent
                event.notification_error = error

        db.commit()
        return {
            "records_processed": len(results),
            "source": source.name,
            "healthy": run.healthy_count,
            "stale": run.stale_count,
            "long_dead": run.long_dead_count,
            "unknown": run.unknown_count,
            "alerts": len(alert_folders),
            "recoveries": len(recovery_folders),
        }
    except Exception as exc:
        run.status = "fail"
        run.ended_at = datetime.now(timezone.utc)
        run.error_message = str(exc)[:2000]
        db.commit()
        logger.error("Record check failed for source %s: %s", source.name, exc, exc_info=True)
        raise


def run_all_record_checks(db: Session, send_notifications: bool = True) -> dict:
    sources = db.query(RecordSource).filter(RecordSource.enabled.is_(True)).order_by(RecordSource.name).all()
    summary = {
        "records_processed": 0,
        "sources_checked": 0,
        "sources_failed": 0,
        "details": [],
    }

    for source in sources:
        try:
            result = run_record_source_check(db, source, send_notifications=send_notifications)
            summary["records_processed"] += int(result.get("records_processed", 0))
            summary["sources_checked"] += 1
            summary["details"].append(result)
        except Exception as exc:
            summary["sources_failed"] += 1
            summary["details"].append({"source": source.name, "status": "fail", "error": str(exc)})

    return summary


def cleanup_old_record_checks(db: Session, retention_days: int) -> int:
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted_events = (
        db.query(RecordStatusEvent)
        .filter(RecordStatusEvent.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    deleted_checks = (
        db.query(RecordFolderCheck)
        .filter(RecordFolderCheck.checked_at < cutoff)
        .delete(synchronize_session=False)
    )
    old_runs = (
        db.query(RecordCheckRun)
        .filter(RecordCheckRun.started_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted_events or 0) + int(deleted_checks or 0) + int(old_runs or 0)
