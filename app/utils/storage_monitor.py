import logging
import os

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import psutil

from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.logging_config import LOG_DIR
from app.models.storage_metric import StorageAlert, StorageMetric

logger = logging.getLogger("storage_monitor")

# Default thresholds
DEFAULT_CRITICAL_PERCENT = 95
DEFAULT_WARNING_PERCENT = 85
DEFAULT_INFO_PERCENT = 75
DEFAULT_CRITICAL_FREE_GB = 5


def get_disk_usage(path: str = "/") -> Tuple[int, int, int, float]:
    """
    Get disk usage statistics.
    Returns: (total_bytes, used_bytes, free_bytes, usage_percent)
    """
    usage = psutil.disk_usage(path)
    return usage.total, usage.used, usage.free, usage.percent


def calculate_directory_size(path: str) -> int:
    """
    Calculate total size of a directory in bytes.
    """
    total_size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                if not os.path.islink(filepath):
                    total_size += os.path.getsize(filepath)
    except Exception as e:
        logger.warning("Error calculating size for %s: %s", path, e)
    return total_size


def get_storage_breakdown() -> Dict[str, int]:
    """
    Get storage breakdown by category.
    Returns dict with bytes for each category.

    Reads application directories independently of the process working directory.
    """
    breakdown = {"snapshots": 0, "videos": 0, "logs": 0, "other": 0}

    # Use the same application-root paths as Compose, regardless of process cwd.
    project_root = Path(__file__).resolve().parents[2]
    storage_paths = {
        "snapshots": project_root / "static" / "snapshots",
        "videos": project_root / "static" / "videos",
        "logs": LOG_DIR,
    }
    for category, path in storage_paths.items():
        if path.is_dir():
            breakdown[category] = calculate_directory_size(str(path))
            logger.debug("%s path: %s, size: %s", category, path, format_bytes(breakdown[category]))

    logger.info(
        "Storage breakdown: snapshots=%s, videos=%s, logs=%s",
        format_bytes(breakdown["snapshots"]),
        format_bytes(breakdown["videos"]),
        format_bytes(breakdown["logs"]),
    )

    return breakdown


def calculate_growth_rate(db: Session, days: int = 7) -> float:
    """
    Calculate average daily growth rate in GB/day.
    """
    try:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        # Get oldest and newest metrics within the period
        oldest = (
            db.query(StorageMetric)
            .filter(StorageMetric.timestamp >= cutoff_date)
            .order_by(StorageMetric.timestamp.asc())
            .first()
        )

        newest = (
            db.query(StorageMetric)
            .filter(StorageMetric.timestamp >= cutoff_date)
            .order_by(StorageMetric.timestamp.desc())
            .first()
        )

        if not oldest or not newest or oldest.id == newest.id:
            return 0.0

        time_diff_days = (newest.timestamp - oldest.timestamp).total_seconds() / 86400
        if time_diff_days < 0.1:  # Less than ~2.4 hours
            return 0.0

        size_diff_gb = (newest.used_bytes - oldest.used_bytes) / (1024**3)
        growth_rate = size_diff_gb / time_diff_days

        return max(0, growth_rate)  # Don't report negative growth

    except Exception as e:
        logger.warning("Error calculating growth rate: %s", e)
        return 0.0


def predict_days_until_full(free_bytes: int, growth_rate_gb_per_day: float) -> float:
    """
    Predict days until storage is full based on growth rate.
    """
    if growth_rate_gb_per_day <= 0:
        return float("inf")

    free_gb = free_bytes / (1024**3)
    days = free_gb / growth_rate_gb_per_day
    return days


def check_storage_thresholds(
    usage_percent: float, free_gb: float, db: Session
) -> Tuple[str, Optional[str]]:
    """
    Check storage against configured thresholds.
    Returns: (alert_level, alert_message)
    """
    critical_percent = int(get_config("storage_critical_percent", DEFAULT_CRITICAL_PERCENT))
    warning_percent = int(get_config("storage_warning_percent", DEFAULT_WARNING_PERCENT))
    info_percent = int(get_config("storage_info_percent", DEFAULT_INFO_PERCENT))
    critical_free_gb = int(get_config("storage_critical_free_gb", DEFAULT_CRITICAL_FREE_GB))

    # Check for critical (either high usage or very low free space)
    if usage_percent >= critical_percent or free_gb < critical_free_gb:
        # Check if we already have an unresolved critical alert
        existing = (
            db.query(StorageAlert)
            .filter(StorageAlert.level == "critical", StorageAlert.resolved_at.is_(None))
            .first()
        )

        if not existing:
            return "critical", f"Storage CRITICAL: {usage_percent:.1f}% used ({free_gb:.1f}GB free)"
        return "critical", None  # Already have alert, don't duplicate message

    elif usage_percent >= warning_percent:
        existing = (
            db.query(StorageAlert)
            .filter(StorageAlert.level == "warning", StorageAlert.resolved_at.is_(None))
            .first()
        )

        if not existing:
            return "warning", f"Storage WARNING: {usage_percent:.1f}% used ({free_gb:.1f}GB free)"
        return "warning", None

    elif usage_percent >= info_percent:
        return "info", f"Storage INFO: {usage_percent:.1f}% used"

    return "normal", None


def record_storage_metric(db: Session) -> StorageMetric:
    """
    Record current storage metrics to database.
    Returns the created metric.
    """
    logger.info("Starting storage metric recording...")

    # Get disk usage
    try:
        default_path = Path(__file__).resolve().parents[2] / "static" / "snapshots"
        monitor_path = os.environ.get("STORAGE_MONITOR_PATH") or str(default_path)
        total, used, free, percent = get_disk_usage(monitor_path)
        logger.info(
            "Disk usage: %.1f%% (%s used, %s free)", percent, format_bytes(used), format_bytes(free)
        )
    except Exception as e:
        logger.error("Failed to get disk usage: %s", e)
        raise

    # Get breakdown
    breakdown = get_storage_breakdown()

    # Calculate growth rate
    growth_rate = calculate_growth_rate(db)

    # Predict days until full
    days_until = predict_days_until_full(free, growth_rate)

    # Check thresholds
    free_gb = free / (1024**3)
    alert_level, alert_msg = check_storage_thresholds(percent, free_gb, db)

    # Create metric record
    metric = StorageMetric(
        total_bytes=total,
        used_bytes=used,
        free_bytes=free,
        usage_percent=percent,
        snapshots_bytes=breakdown["snapshots"],
        videos_bytes=breakdown["videos"],
        logs_bytes=breakdown["logs"],
        other_bytes=breakdown["other"],
        daily_growth_rate=growth_rate,
        days_until_full=days_until if days_until != float("inf") else 9999,
        alert_level=alert_level,
        alert_message=alert_msg,
    )

    db.add(metric)
    db.commit()

    # Create alert if needed
    if alert_msg and alert_level in ["warning", "critical"]:
        alert = StorageAlert(
            level=alert_level, message=alert_msg, usage_percent=percent, free_gb=free_gb
        )
        db.add(alert)
        db.commit()

        logger.warning("[STORAGE ALERT] %s: %s", alert_level.upper(), alert_msg)

    logger.info(
        "[STORAGE] Usage: %.1f%% (%s free), Growth: %.2f GB/day, Predicted full in: %.1f days",
        percent,
        format_bytes(free),
        growth_rate,
        days_until if days_until != float("inf") else -1,
    )

    return metric


def format_bytes(bytes_val: int) -> str:
    """
    Format bytes to human readable string.
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def get_latest_storage_status(db: Session) -> Optional[StorageMetric]:
    """
    Get the most recent storage metric.
    """
    return db.query(StorageMetric).order_by(StorageMetric.timestamp.desc()).first()


def get_storage_trend(db: Session, days: int = 7) -> list:
    """
    Get storage trend for the last N days.
    Returns list of metrics (one per day).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Get daily snapshots (latest per day)
    metrics = (
        db.query(StorageMetric)
        .filter(StorageMetric.timestamp >= cutoff)
        .order_by(StorageMetric.timestamp.asc())
        .all()
    )

    return metrics


def resolve_storage_alerts(db: Session, username: str):
    """
    Mark all unresolved storage alerts as resolved.
    Called after manual cleanup.
    """
    unresolved = db.query(StorageAlert).filter(StorageAlert.resolved_at.is_(None)).all()

    now = datetime.now(timezone.utc)
    for alert in unresolved:
        alert.resolved_at = now
        alert.resolved_by = username

    if unresolved:
        db.commit()
        logger.info("Resolved %d storage alerts by %s", len(unresolved), username)
