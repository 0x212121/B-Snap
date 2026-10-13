"""Recurring WhatsApp command jobs using the native bot access and delivery helpers."""

from __future__ import annotations

import asyncio

from typing import Any

from apscheduler.triggers.cron import CronTrigger

from app.db.database import SessionLocal
from app.models.job_execution_log import JobExecutionLog
from app.models.log import CommandLog
from app.utils.wa_bot_commands import (
    command_schedule_cron,
    get_command_settings,
    normalize_command_schedules,
    schedule_execution_sender,
)

JOB_PREFIX = "wa_command:"


def command_job_id(command_id: str, schedule_id: str = "legacy") -> str:
    """Keep existing history keys while assigning independent new schedule IDs."""
    suffix = "" if schedule_id == "legacy" else f":{schedule_id}"
    return f"{JOB_PREFIX}{command_id}{suffix}"


def scheduled_command_name(command: dict) -> str:
    """Identify each schedule by its arguments and recipient in Job Management."""
    name = f"WhatsApp: {command['name']}"
    schedule = command["schedule"]
    if schedule.get("id", "legacy") != "legacy":
        name += f" - {schedule['argument'][:80] or '(no arguments)'} -> {schedule['recipient']}"
    return name[:200]


def scheduled_commands(db: Any) -> dict[str, dict]:
    """Return enabled commands with validated, enabled schedules."""
    result = {}
    for command in get_command_settings(db):
        if not command.get("enabled"):
            continue
        for schedule in normalize_command_schedules(command, command.get("required_params", [])):
            if schedule["enabled"]:
                job_id = command_job_id(command["id"], schedule["id"])
                result[job_id] = {**command, "schedule": schedule}
    return result


def sync_command_jobs(scheduler: Any) -> None:
    """Reconcile jobs on startup and reload without resetting unchanged next runs."""
    with SessionLocal() as db:
        commands = scheduled_commands(db)
    for job in scheduler.get_jobs():
        if job.id.startswith(JOB_PREFIX) and job.id not in commands:
            scheduler.remove_job(job.id)
    for job_id, command in commands.items():
        trigger = CronTrigger.from_crontab(
            command_schedule_cron(command["schedule"]), timezone=scheduler.timezone
        )
        job = scheduler.get_job(job_id)
        if job is None:
            scheduler.add_job(
                run_scheduled_command,
                trigger=trigger,
                id=job_id,
                args=(
                    [command["id"]]
                    if command["schedule"]["id"] == "legacy"
                    else [command["id"], command["schedule"]["id"]]
                ),
                name=scheduled_command_name(command),
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        elif job.name != scheduled_command_name(command):
            job.modify(name=scheduled_command_name(command))
        if job is not None and (
            str(job.trigger) != str(trigger) or str(job.trigger.timezone) != str(trigger.timezone)
        ):
            job.reschedule(trigger=trigger)


async def execute_scheduled_command(db: Any, command: dict) -> int:
    """Execute as the recipient's current whitelist identity and log each delivery."""
    # Import lazily to keep scheduler registration independent of route initialization.
    from app.routes.wa_webhook import (  # noqa: PLC0415
        WABotHandler,
        _command_access_error,
        _record_wa_message,
        _send_wa_message_item,
        _whitelisted_sender,
    )
    from app.utils.wa_executor import run_gateway_blocking  # noqa: PLC0415
    from app.utils.wa_gateway import WAGatewayService  # noqa: PLC0415

    schedule = command["schedule"]
    recipient = schedule["recipient"]
    sender = schedule_execution_sender(command, schedule)
    error = _command_access_error(_whitelisted_sender(db, sender), command)
    if error:
        raise ValueError(error)
    service = WAGatewayService(db)
    if not service.config.enabled:
        raise ValueError("WhatsApp gateway is disabled")
    message = f"{command['trigger']} {schedule['argument']}".strip()
    db.add(CommandLog(user_id=sender, command=command["trigger"], source="whatsapp_schedule"))
    db.commit()
    bot = WABotHandler(db)
    # Pin the saved command by ID: overlapping triggers must not select another action.
    bot._test_command = command
    bot._test_argument = schedule["argument"]
    response = await bot.handle(sender, message)
    items = list(bot.outbound_items)
    if not items and (bot.media_items or bot.media_path):
        media = bot.media_items or [
            {"path": bot.media_path, "caption": bot.media_caption or response}
        ]
        items = [{"image_path": item["path"], "text": item.get("caption", "")} for item in media]
    elif not items and response:
        items = [{"text": response}]
    db.commit()
    delivered = 0
    failed = False
    for item in items:
        result = await run_gateway_blocking(_send_wa_message_item, service, recipient, item)
        success = bool(result.get("success"))
        _record_wa_message(
            db,
            recipient,
            "outbound",
            "accepted" if success else "failed",
            item.get("text") or "[media]",
            command["trigger"],
            result.get("error", ""),
        )
        db.commit()
        delivered += int(success)
        failed |= not success
    if failed or bot.action_failed or not items:
        raise RuntimeError("Scheduled WhatsApp command execution or delivery failed")
    return delivered


def run_scheduled_command(command_id: str, schedule_id: str = "legacy") -> None:
    """Reload settings and permissions at run time and record job execution history."""
    job_id = command_job_id(command_id, schedule_id)
    with SessionLocal() as db:
        command = scheduled_commands(db).get(job_id)
        if not command:
            return
        log = JobExecutionLog.start_execution(db, job_id, scheduled_command_name(command))
        db.commit()
        try:
            count = asyncio.run(execute_scheduled_command(db, command))
            log.complete(db, "success", records=count)
        except Exception:
            db.rollback()
            log.complete(
                db, "fail", error="Scheduled WhatsApp command failed; check application logs."
            )
            raise
