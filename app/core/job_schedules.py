"""Schedule settings shared by the scheduler and job management API."""

from __future__ import annotations

from datetime import tzinfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_config

# job ID: cron config key, interval config key, interval unit, interval default
JOB_SCHEDULES = {
    "scheduled_snapshot": ("snapshot_cron", "snapshot_interval_minutes", "minutes", 480),
    "health_check": ("healthcheck_cron", "healthcheck_interval_minutes", "minutes", 15),
    "storage_check": ("storage_check_cron", "storage_check_interval_hours", "hours", 1),
    "record_folder_check": ("record_check_cron", "record_check_interval_minutes", "minutes", 10),
    "email_retry": ("email_retry_cron", "email_retry_interval_minutes", "minutes", 1),
    "cleanup_camera_stats": ("cleanup_camera_stats_cron", "cleanup_interval_days", "days", 1),
    "cleanup_api_logs": ("cleanup_api_logs_cron", "cleanup_interval_days", "days", 1),
    "cleanup_command_logs": ("cleanup_command_logs_cron", "cleanup_interval_days", "days", 1),
    "cleanup_record_checks": ("cleanup_record_checks_cron", "cleanup_interval_days", "days", 1),
    "cleanup_email_retry": (
        "cleanup_email_retry_cron",
        "cleanup_retry_queue_interval_days",
        "days",
        1,
    ),
    "wa_daily_report": ("wa_daily_report_cron", None, None, None),
    "record_check_daily_report": ("record_check_daily_report_cron", None, None, None),
    "orphaned_snapshots_check": ("orphaned_snapshots_check_cron", None, "hours", 1),
    "retention_policy": ("retention_policy_cron", None, None, None),
}


def get_job_schedule(job_id: str) -> dict:
    """Return the effective schedule, preserving legacy defaults when unset."""
    cron_key, interval_key, unit, default = JOB_SCHEDULES[job_id]
    cron = str(get_config(cron_key, "") or "").strip()
    if not cron:
        if job_id in {
            "cleanup_camera_stats",
            "cleanup_api_logs",
            "cleanup_command_logs",
            "cleanup_record_checks",
        }:
            cron = str(get_config("cleanup_cron", "0 2 * * *") or "").strip()
        elif job_id in {"wa_daily_report", "record_check_daily_report"}:
            hour = int(get_config("wa_daily_report_hour", 8))
            minute = int(get_config("wa_daily_report_minute", 0))
            cron = f"{minute} {hour} * * *"
        elif job_id == "retention_policy":
            cron = "0 3 * * *"
    interval = int(get_config(interval_key, default)) if interval_key else default
    return {"cron_expression": cron, "interval_unit": unit, "interval": interval}


def build_job_trigger(settings: dict, timezone: tzinfo) -> CronTrigger | IntervalTrigger:
    """Build a timezone-aware trigger from effective schedule settings."""
    if settings["cron_expression"]:
        return CronTrigger.from_crontab(settings["cron_expression"], timezone=timezone)
    return IntervalTrigger(**{settings["interval_unit"]: settings["interval"]}, timezone=timezone)
