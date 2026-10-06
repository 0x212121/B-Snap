"""
GoWA Webhook Handler - WhatsApp Bot Integration
Handles incoming messages from GoWA webhook and responds to commands.
"""
import logging
import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Awaitable, Callable, Optional

from app.db.database import get_db, SessionLocal
from app.models.user import User
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required, get_current_user
from app.routes.ping import ping_ip
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.utils.response_helper import json_success_response, json_error_response
from app.utils.wa_bot_commands import (
    CUSTOM_ACTION_ROLES,
    get_command_settings,
    match_command,
    normalize_snap_stage_messages,
    render_response,
    save_command_settings,
)
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.video import Video
from app.models.log import CommandLog, WhatsAppMessageLog
from app.models.config import Configuration
from app.utils.snapshot_service import SnapshotService
from app.utils.audit_logger import log_audit
from app.utils.video import STATIC_VIDEO_DIR
from app.utils.wa_executor import run_gateway_blocking
from app.utils.wa_bot_workflow import render_flow_template, run_api_flow
from app.utils.wa_bot_workflow import APIFlowError, bind_command_arguments, normalize_flow
from app.utils.healthcheck import format_uptime_duration
from app.utils.timezone_helper import get_current_timezone
import os

logger = logging.getLogger("main")

router = APIRouter(tags=["WhatsApp"])
WA_API_TOKEN_OWNER_KEY = "wa_bot_api_token_owner_id"
WA_API_TOKEN_ID_KEY = "wa_bot_api_token_id"


def _normalize_wa_sender(sender: str) -> str:
    """Normalize phone numbers and device-qualified GoWA JIDs identically."""
    return format_phone_number(re.split(r"[@:]", str(sender).strip(), maxsplit=1)[0])


def _whitelisted_sender(db: Session, sender: str) -> WhatsappWhitelist | None:
    phone = _normalize_wa_sender(sender)
    if not phone:
        return None
    return db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone).first()


def _command_access_error(entry: WhatsappWhitelist | None, command: dict) -> str:
    if not entry:
        return "Sender phone number is required and must be on the WhatsApp whitelist."
    role = CUSTOM_ACTION_ROLES.get(command.get("action"), "user")
    if (role == "admin" or command.get("role") == "admin") and entry.role != RoleEnum.admin:
        return "This command is available to WhatsApp whitelist admins only."
    return ""


def _access_error_response(message: str) -> JSONResponse:
    error = {"status_code": 403, "body": {"status": "error", "success": False,
             "message": message, "detail": message}}
    return JSONResponse(status_code=403, content={"status": "error", "detail": message,
                        "steps": {"error": error, "result": error}})


def _record_wa_message(db: Session, phone: str, direction: str, status: str, message: str = "", command: str = "", error: str = "", provider_message_id: str | None = None) -> None:
    db.add(WhatsAppMessageLog(phone_number=phone[:32], provider_message_id=provider_message_id,
                              direction=direction, status=status,
                              message=message[:4000] or None, command=command[:120] or None,
                              error=error[:1000] or None))


def _send_wa_message_item(service: WAGatewayService, recipient: str, item: dict, reply_to: str | None = None) -> dict:
    caption = str(item.get("text") or "")
    if item.get("video_path"):
        return service.send_video_file(recipient, item["video_path"], caption or None, reply_to=reply_to)
    if item.get("image_path"):
        return service.send_image_file(recipient, item["image_path"], caption or None, reply_to=reply_to)
    if item.get("image_url"):
        return service.send_image(recipient, item["image_url"], caption or None, reply_to=reply_to)
    if caption:
        return service.send_text(recipient, caption, reply_to=reply_to)
    return {"success": False, "error": "Message item has no text or media"}


def _send_wa_progress_item(recipient: str, item: dict, sender: str, command: str, reply_to: str | None) -> dict:
    """Send and log progress with a session owned by the gateway worker."""
    with SessionLocal() as progress_db:
        result = _send_wa_message_item(WAGatewayService(progress_db), recipient, item, reply_to)
        _record_wa_message(
            progress_db, sender, "outbound", "accepted" if result.get("success") else "failed",
            item.get("text") or "[image]", command, result.get("error", ""),
        )
        progress_db.commit()
        return result


def _analytics_command_text(message: str) -> str:
    """Retain arguments for analytics while redacting credential values."""
    safe_message = message.strip()
    return re.sub(
        r"(?i)(\b(?:pass(?:word)?|token|secret|credential)\s*:\s*)[^,]+",
        r"\1[redacted]",
        safe_message,
    )[:500]


class GoWATestConfig(BaseModel):
    """Unsaved GoWA settings submitted for a connection check."""

    enabled: bool = True
    base_url: str
    api_key: str = ""
    device_id: str = ""


def _config_value(db: Session, key: str) -> Optional[str]:
    row = db.query(Configuration).filter(Configuration.key == key).first()
    return row.value if row else None


def _set_config_value(db: Session, key: str, value: str) -> None:
    row = db.query(Configuration).filter(Configuration.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Configuration(key=key, value=value))


@router.get("/api/admin/wa-bot/commands")
def get_wa_bot_commands(db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    token_owner_id = _config_value(db, WA_API_TOKEN_OWNER_KEY)
    token_id = _config_value(db, WA_API_TOKEN_ID_KEY)
    selected_token = token_id if token_owner_id == str(current_admin.id) else ""
    return {
        "commands": get_command_settings(db),
        "api_tokens": [
            {"id": item.get("id"), "name": item.get("name", "API token"), "prefix": item.get("prefix", "")}
            for item in (current_admin.api_tokens or [])
            if isinstance(item, dict) and item.get("id")
        ],
        "api_token_id": selected_token,
    }


@router.put("/api/admin/wa-bot/commands")
async def put_wa_bot_commands(request: Request, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    try:
        payload = await request.json()
        commands = save_command_settings(db, payload.get("commands", []))
        token_id = str(payload.get("api_token_id") or "").strip()
        if token_id and not any(
            isinstance(item, dict) and str(item.get("id")) == token_id
            for item in (current_admin.api_tokens or [])
        ):
            raise ValueError("The API token must belong to the currently signed-in admin")
        _set_config_value(db, WA_API_TOKEN_OWNER_KEY, str(current_admin.id) if token_id else "")
        _set_config_value(db, WA_API_TOKEN_ID_KEY, token_id)
        db.commit()
        return {"status": "success", "commands": commands}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/admin/wa-bot/messages")
def get_wa_bot_messages(limit: int = 100, db: Session = Depends(get_db), current_admin: User = Depends(admin_access_required)):
    limit = max(1, min(limit, 250))
    rows = db.query(WhatsAppMessageLog).order_by(WhatsAppMessageLog.timestamp.desc(), WhatsAppMessageLog.id.desc()).limit(limit).all()
    return {"messages": [{"id": row.id, "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        "phone_number": row.phone_number, "direction": row.direction, "status": row.status,
        "command": row.command, "message": row.message, "error": row.error} for row in rows]}


@router.post("/api/admin/wa-bot/test-flow")
async def test_wa_bot_flow(request: Request, current_admin: User = Depends(admin_access_required),
                           db: Session = Depends(get_db)):
    """Run an unsaved WhatsApp API flow once for the builder preview."""
    try:
        payload = await request.json()
        sender = _normalize_wa_sender(str(payload.get("sender") or ""))
        access_error = _command_access_error(_whitelisted_sender(db, sender), payload.get("command") or {"action": "api_flow"})
        if access_error:
            return _access_error_response(access_error)
        flow = normalize_flow(payload.get("flow"))
        token_id = str(payload.get("api_token_id") or "").strip()
        token = None
        if token_id:
            token = next(
                (
                    item.get("token")
                    for item in (current_admin.api_tokens or [])
                    if isinstance(item, dict) and str(item.get("id")) == token_id
                ),
                None,
            )
            if not token:
                raise ValueError("The API token was not found in the currently signed-in admin account")
        context = await run_api_flow(
            flow=flow,
            bearer_token=token,
            sender=sender,
            argument=str(payload.get("argument") or "").strip()[:500],
            parameters=bind_command_arguments(
                payload.get("required_params") or [], str(payload.get("argument") or "").strip()[:500]
            ),
        )
        return {"status": "success", "steps": context["steps"], "params": context.get("params", {})}
    except APIFlowError as exc:
        return JSONResponse(status_code=422, content={
            "status": "error", "detail": str(exc), "steps": exc.context["steps"],
            "params": exc.context.get("params", {}),
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("WhatsApp Bot API flow test failed for admin %s: %s", current_admin.id, exc)
        detail = str(exc) or "The API flow could not be run"
        raise HTTPException(status_code=502, detail=detail[:500]) from exc


@router.post("/api/admin/wa-bot/test-action")
async def test_wa_bot_action(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Execute an unsaved native action and preview success/failure without WhatsApp sends."""
    payload = await request.json()
    command = payload.get("command")
    if not isinstance(command, dict) or not isinstance(command.get("action"), str) or command.get("action") not in CUSTOM_ACTION_ROLES or command.get("action") == "api_flow":
        raise HTTPException(status_code=422, detail="Select a supported native command action")
    action = command["action"]
    if action in {"snap", "edit", "whitelist_add", "whitelist_remove"} and payload.get("execute") is not True:
        raise HTTPException(status_code=422, detail="This test executes the action; set execute=true")
    argument = str(payload.get("argument") or "").strip()[:500]
    try:
        parameters = bind_command_arguments(command.get("required_params") or [], argument)
        if not isinstance(command.get("response", ""), str):
            raise ValueError("Response template must be text")
        command = {**command,
                   "failure_messages": normalize_snap_stage_messages(
                       command.get("failure_messages"), default_text="Snapshot capture failed.", allow_empty=action != "snap"
                   ),
                   "image_flow": normalize_flow(command["image_flow"]) if command.get("image_flow") else []}
    except ValueError as exc:
        error = {"status_code": 422, "body": {"status": "error", "message": str(exc), "detail": str(exc)}}
        return JSONResponse(status_code=422, content={"status": "error", "detail": str(exc), "steps": {"error": error, "result": error}})
    bot = WABotHandler(db)
    bot._is_builder_test = True
    bot._test_command = {**command, "trigger": command.get("trigger") or "/test",
                         "response_type": "text", "processing_messages": None if action == "snap" else [],
                         "processing_delay_seconds": 0}
    bot._test_argument = argument
    token_id = str(payload.get("api_token_id") or "").strip()
    if token_id:
        token = next((item.get("token") for item in (current_admin.api_tokens or [])
                      if isinstance(item, dict) and str(item.get("id")) == token_id), None)
        if not token:
            raise HTTPException(status_code=422, detail="The selected API token was not found")
        bot._selected_api_token = lambda: token
    # Command access comes from the sender whitelist, as it does on GoWA.
    sender = _normalize_wa_sender(str(payload.get("sender") or ""))
    async def suppress_delivery(item: dict) -> dict:
        return {"success": True}
    bot.progress_sender = suppress_delivery
    try:
        response = await bot.handle(sender, f"/test {argument}")
        if not bot.action_failed:
            db.commit()
        else:
            db.rollback()
    except Exception as exc:
        db.rollback()
        logger.exception("Native action test failed: %s", action)
        bot._set_action_error("The action failed. Check application logs.", 502)
        response = ""
    steps = bot.action_api_context.get("steps", {})
    if not steps:
        steps = {"result": {"status_code": 200, "body": {"status": "success", "message": response}}}
    failed = bot.action_failed
    error = steps.get("error", {})
    code = error.get("status_code", 400) if failed else 200
    return JSONResponse(status_code=code, content={
        "status": "error" if failed else "success", "steps": steps,
        "params": parameters, "detail": error.get("body", {}).get("message", "") if failed else "",
        "response_preview": response or bot.media_caption or "",
        "failure_previews": bot._render_action_stage_messages(command.get("failure_messages") or [], argument, sender,
            error.get("body", {}).get("message", "")) if failed else [],
    })


class WABotHandler:
    """Handler for WhatsApp bot commands."""
    
    def __init__(self, db: Session):
        self.db = db
        self.media_path: Optional[str] = None
        self.media_caption: Optional[str] = None
        self.media_items: list[dict[str, str]] = []
        self.outbound_items: list[dict[str, str]] = []
        self.private_response = False
        self.quote_reply = True
        self.progress_sender: Optional[Callable[[dict[str, str]], Awaitable[dict]]] = None
        self.action_failed = False
        self.action_api_context: dict = {"steps": {}}
        self._processing_task: asyncio.Task | None = None
        self._processing_suppressed = False
        self._queued_processing: list[dict] = []
        self._is_builder_test = False
        self._test_command: dict | None = None
        self._test_argument = ""

    def _merge_action_context(self, context: dict) -> dict:
        base = self.action_api_context or {}
        merged_steps = {**(base.get("steps") or {}), **(context.get("steps") or {})}
        self.action_api_context = {**base, **context, "steps": merged_steps}
        return self.action_api_context

    def _set_action_error(self, message: str, status_code: int = 400) -> None:
        """Expose a stable error result to tests and failure-message templates."""
        self.action_failed = True
        error = {"status_code": status_code, "body": {
            "status": "error", "success": False, "message": message, "detail": message,
        }}
        steps = {"error": error}
        if "result" not in self.action_api_context.get("steps", {}):
            steps["result"] = error
        self._merge_action_context({"steps": steps})

    def _render_action_stage_messages(self, messages: list[dict], argument: str, sender: str,
                                      error: str = "") -> list[dict[str, str]]:
        context = self._merge_action_context({"sender": sender, "argument": argument, "steps": {}})
        timezone_name = get_current_timezone(self.db)
        result = []
        for item in messages:
            values = {"argument": argument, "sender": sender, "camera": argument, "ip": "", "error": error}
            text = render_response(render_flow_template(str(item.get("text") or ""), context, timezone_name), **values)
            image_type = item.get("image_source_type", "none")
            rendered: dict[str, str] = {"text": text}
            if image_type in {"url", "api"}:
                image_url = render_response(render_flow_template(str(item.get("image_source") or ""), context, timezone_name), **values).strip()
                parsed = urlsplit(image_url)
                if not parsed.scheme and not parsed.netloc:
                    public_base = (_config_value(self.db, "app_public_url") or "").strip()
                    if public_base:
                        image_url = urljoin(public_base.rstrip("/") + "/", image_url.lstrip("/"))
                        parsed = urlsplit(image_url)
                if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                    rendered["image_url"] = image_url
            elif image_type == "last_snapshot":
                camera = self._find_camera_for_capture(argument) if argument else None
                snapshot = self._latest_snapshot_with_file(camera.id) if camera else None
                if snapshot:
                    rendered["image_path"] = os.path.join("static", "snapshots", snapshot.file_path)
            if rendered.get("text") or rendered.get("image_url") or rendered.get("image_path"):
                result.append(rendered)
        return result

    async def _send_action_stage(self, messages: list[dict], argument: str, sender: str,
                                 error: str = "") -> None:
        await self._cancel_processing()
        for item in self._render_action_stage_messages(messages, argument, sender, error):
            if self.progress_sender:
                try:
                    await self.progress_sender(item)
                except Exception:
                    logger.exception("Failed to send WhatsApp action-stage message")
            else:
                self.outbound_items.append(item)

    async def _cancel_processing(self) -> None:
        """Suppress pending processing messages before final or fallback replies."""
        self._processing_suppressed = True
        task = self._processing_task
        if task and not task.done():
            task.cancel()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._processing_task = None
        queued_ids = {id(item) for item in self._queued_processing}
        self.outbound_items = [item for item in self.outbound_items if id(item) not in queued_ids]
        self._queued_processing.clear()

    async def _schedule_processing_items(self, items: list[dict], delay: int) -> None:
        """Send processing only if the operation is still running after the delay."""
        if not items:
            return

        async def send() -> None:
            if delay:
                await asyncio.sleep(delay)
            for item in items:
                if self._processing_suppressed or self.action_failed:
                    return
                if self.progress_sender:
                    try:
                        await self.progress_sender(item)
                    except Exception:
                        logger.exception("Failed to send WhatsApp processing message")
                else:
                    self.outbound_items.append(item)
                    self._queued_processing.append(item)

        if delay:
            self._processing_task = asyncio.create_task(send())
        else:
            await send()

    async def _schedule_action_processing(self, command: dict, argument: str, sender: str) -> None:
        items = self._render_action_stage_messages(command.get("processing_messages") or [], argument, sender)
        await self._schedule_processing_items(items, command.get("processing_delay_seconds", 0))
    
    def parse_command(self, message: str) -> tuple[str, list]:
        """
        Parse command from message.
        Returns: (command, args)
        """
        # Normalize message
        text = message.strip().lower()
        parts = text.split()
        
        if not parts:
            return "", []
        
        # First word is command (remove / or ! prefix)
        cmd = re.sub(r'^[/!]', '', parts[0])
        args = parts[1:] if len(parts) > 1 else []
        
        return cmd, args
    
    async def handle(self, sender: str, message: str, is_group: bool = False) -> str:
        self._processing_suppressed = False
        try:
            return await self._handle_response(sender, message, is_group)
        finally:
            await self._cancel_processing()

    async def _handle_response(self, sender: str, message: str, is_group: bool = False) -> str:
        """Execute the command and apply its configured success response media."""
        self._response_command = None
        response = await self._handle_command(sender, message, is_group)
        command = self._response_command
        if not command or self.action_failed or command.get("response_type", "text") == "text":
            return response
        caption = response or self.media_caption or ""
        success_paths = {item["path"] for item in self.media_items}
        if self.media_path:
            success_paths.add(self.media_path)
        self.outbound_items = [item for item in self.outbound_items if item.get("image_path") not in success_paths]
        self.media_path = None
        self.media_items = []
        try:
            if command["response_type"] == "image":
                await self._attach_response_image(command, caption)
            elif command["response_type"] == "video":
                await self._attach_response_video(command, caption)
            return ""
        except Exception as exc:
            self._set_action_error(str(exc), exc.status_code if isinstance(exc, HTTPException) else 502)
            logger.warning("WhatsApp configured media response failed: %s", exc)
            if command.get("failure_messages"):
                await self._send_action_stage(command["failure_messages"], self._command_argument, sender, str(exc))
                return ""
            return f"Response media failed: {exc}"

    async def _handle_command(self, sender: str, message: str, is_group: bool = False) -> str:
        """
        Handle incoming message and return response.
        
        Args:
            sender: Sender phone number
            message: Message text
        
        Returns:
            Response message
        """
        sender = _normalize_wa_sender(sender)
        self._sender = sender
        entry = _whitelisted_sender(self.db, sender)
        if not entry:
            message = _command_access_error(entry, {})
            self._set_action_error(message, 403)
            return message
        commands = [self._test_command] if self._test_command else get_command_settings(self.db)
        command, argument = (self._test_command, self._test_argument) if self._test_command else match_command(message, commands)
        if command:
            if is_group and command.get("chat_scope") == "private":
                logger.info("Ignoring private-only WhatsApp command in group: %s", command.get("trigger"))
                return ""
            self.quote_reply = bool(command.get("quote_reply", True))
            entry = _whitelisted_sender(self.db, sender)
            is_admin = bool(entry and entry.role == RoleEnum.admin)
            access_error = _command_access_error(entry, command)
            if access_error:
                self._set_action_error(access_error, 403)
                return access_error
            required_params = command.get("required_params") or []
            argument_count = len(argument.split())
            missing_required_params = bool(required_params) and (
                not argument.strip()
                or (len(required_params) > 1 and argument_count < len(required_params))
            )
            if missing_required_params:
                usage = " ".join(f"<{parameter}>" for parameter in required_params)
                return f"Required parameters are missing. Usage: {command['trigger']} {usage}"
            action = command.get("action")
            self._response_command = command
            self._command_argument = argument
            self.action_failed = False
            self.action_api_context = {
                "sender": sender, "argument": argument, "steps": {},
                "params": bind_command_arguments(required_params, argument),
            }
            action_flow = command.get("flow") if action == "api_flow" else None
            if action_flow:
                self.private_response = True
            if action_flow:
                try:
                    await self._schedule_action_processing(command, argument, sender)
                    self.action_api_context = await run_api_flow(
                        flow=action_flow,
                        bearer_token=self._selected_api_token(),
                        sender=sender,
                        argument=argument,
                        parameters=self.action_api_context.get("params", {}),
                    )
                except Exception as exc:
                    if isinstance(exc, APIFlowError):
                        self.action_api_context = self._merge_action_context(exc.context)
                    logger.warning("WhatsApp action data flow failed for %s: %s", command.get("trigger"), exc)
                    self.action_failed = True
                    self._set_action_error(str(exc), 502)
                    if command.get("failure_messages"):
                        await self._send_action_stage(command["failure_messages"], argument, sender, str(exc))
                        return ""
                    return f"API flow failed: {exc}"
            if action != "snap" and not action_flow:
                await self._schedule_action_processing(command, argument, sender)
            if action == "help":
                enabled = [
                    c for c in commands
                    if c.get("enabled")
                    and (c.get("role") != "admin" or is_admin)
                    and (not is_group or c.get("chat_scope") != "private")
                ]
                help_rows = [{
                    "trigger": c["trigger"],
                    "description": c.get("description") or c["name"],
                    "parameters": " ".join(f"<{parameter}>" for parameter in c.get("required_params", [])),
                } for c in enabled]
                help_context = self._merge_action_context({"steps": {"result": {
                    "status_code": 200, "body": {"commands": help_rows},
                }}})
                heading = self._render_action_response(
                    command.get("response", "*B-Snap Bot Commands*"), help_context,
                    timezone_name=get_current_timezone(self.db), argument=argument, sender=sender,
                )
                if "{{#each steps.result.body.commands}}" in command.get("response", ""):
                    return heading
                return heading + "\n" + "\n".join(
                    f"{c['trigger']}"
                    + (" " + " ".join(f"<{parameter}>" for parameter in c.get("required_params", [])) if c.get("required_params") else "")
                    + f" - {c.get('description') or c['name']}"
                    for c in enabled
                )
            if action == "ping_test":
                return self._render_action_response(
                    command.get("response", "Pong"), self.action_api_context,
                    timezone_name=get_current_timezone(self.db), argument=argument, sender=sender,
                )
            if action == "text":
                return self._render_action_response(
                    command.get("response", ""), self.action_api_context,
                    timezone_name=get_current_timezone(self.db), argument=argument, sender=sender,
                )
            if action == "api_flow":
                return render_flow_template(
                    command.get("response", ""), self.action_api_context,
                    timezone_name=get_current_timezone(self.db),
                )
            try:
                response = await self._handle_n8n_action(action, sender, argument, command)
            except Exception as exc:
                logger.exception("WhatsApp system action failed for %s", command.get("trigger"))
                self.action_failed = True
                response = f"Action failed: {exc}"
                self._set_action_error(response, exc.status_code if isinstance(exc, HTTPException) else 502)
            if self.action_failed and "error" not in self.action_api_context.get("steps", {}):
                self._set_action_error(response or "The action failed")
            if action == "snap" and self.action_failed and not response:
                return ""
            if self.action_failed and command.get("failure_messages"):
                await self._send_action_stage(command["failure_messages"], argument, sender, response)
                return ""
            return response

        for configured in commands:
            if configured.get("enabled"):
                continue
            for phrase in [configured.get("trigger", ""), *configured.get("aliases", [])]:
                phrase = str(phrase).strip().lower()
                if phrase and (message.strip().lower() == phrase or message.strip().lower().startswith(phrase + " ")):
                    return "This command is currently disabled."

        if message.lstrip().startswith(("/", "!")):
            logger.info("WA Bot: %s sent an unconfigured command", sender[-4:])
            return ""
        return ""

    async def _attach_response_image(self, command: dict, caption: str) -> None:
        """Resolve a configured image URL or protected snapshot for any action."""
        source = self._render_action_response(
            command.get("response_image_source", ""), self.action_api_context,
            timezone_name=get_current_timezone(self.db),
            argument=self._command_argument, sender=self._sender,
        ).strip()
        parsed = urlsplit(source)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            self.outbound_items.append({"image_url": source, "text": caption})
            return
        if parsed.scheme or parsed.netloc or not source:
            raise ValueError("The image source must be an HTTP(S) URL or protected snapshot")
        path = unquote(parsed.path)
        if path.startswith("/api/snapshots/secure/"):
            snapshot = self.db.query(Snapshot).filter(
                Snapshot.id == path.removeprefix("/api/snapshots/secure/"),
                Snapshot.deleted_at.is_(None),
            ).first()
        elif path.startswith("/snapshot/file/") or path.startswith("/api/snapshots/file/"):
            file_path = path.removeprefix("/snapshot/file/").removeprefix("/api/snapshots/file/")
            snapshot = self.db.query(Snapshot).filter(
                Snapshot.file_path == file_path, Snapshot.deleted_at.is_(None),
            ).first()
        else:
            raise ValueError("Select a protected snapshot URL or an HTTP(S) image URL")
        if not snapshot:
            raise ValueError("The selected snapshot is no longer available")
        token = self._selected_api_token()
        if command.get("action") == "api_flow":
            if not token:
                raise ValueError("Protected API images require a valid B-Snap API token")
            owner = await get_current_user(
                Request({"type": "http", "session": {}, "headers": []}),
                db=self.db, authorization=f"Bearer {token}",
            )
        else:
            owner = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == self._sender).first()
        if not owner:
            raise ValueError("Image access is not authorized")
        camera = self.db.query(Camera).filter(Camera.id == snapshot.camera_id).first()
        if owner.group_id is not None and (
            not camera or not any(group.id == owner.group_id for group in camera.groups)
        ):
            raise ValueError("Access denied to the selected snapshot")
        root = Path("static/snapshots").resolve()
        local_path = (root / snapshot.file_path).resolve()
        if not local_path.is_relative_to(root) or not local_path.is_file():
            raise ValueError("The selected snapshot file is unavailable")
        log_audit(db=self.db, user=self._sender, action="prepare_snapshot_whatsapp",
                  target=f"{snapshot.camera_name}/{snapshot.id}")
        self.outbound_items.append({"image_path": str(local_path), "text": caption})

    async def _attach_response_video(self, command: dict, caption: str | None = None) -> None:
        """Attach the explicitly selected response video after checking token access."""
        caption = caption if caption is not None else render_flow_template(
            command.get("response", ""), self.action_api_context,
            timezone_name=get_current_timezone(self.db),
        )
        source = render_flow_template(
            command.get("response_video_source", ""), self.action_api_context
        ).strip()
        if source.startswith("/api/videos/secure/"):
            source = source.removeprefix("/api/videos/secure/").split("?", 1)[0]
        if not source or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", source):
            raise ValueError("The selected API field did not return a video ID or secure video URL")
        token = self._selected_api_token()
        if not token:
            raise ValueError("Video responses require a valid B-Snap API token")
        auth_request = Request({"type": "http", "session": {}, "headers": []})
        owner = await get_current_user(auth_request, db=self.db, authorization=f"Bearer {token}")
        if owner.role not in {"operator", "admin"}:
            raise ValueError("Video responses require an operator or admin API token")
        video = self.db.query(Video).filter(
            Video.id == source, Video.deleted_at.is_(None),
        ).first()
        if not video:
            raise ValueError("The selected video is no longer available")
        if owner.group_id is not None and (
            not video.camera or not any(group.id == owner.group_id for group in video.camera.groups)
        ):
            raise ValueError("Access denied to the selected video")
        root = STATIC_VIDEO_DIR.resolve()
        path = (root / video.file_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("The selected video file is unavailable")
        log_audit(
            db=self.db, user=owner.username, action="prepare_video_whatsapp",
            target=f"{video.camera_name}/{video.id}",
            extra="Configured video response prepared for the invoking user's private WhatsApp chat",
        )
        self.outbound_items.append({"video_path": str(path), "text": caption})

    def _selected_api_token(self) -> Optional[str]:
        owner_id = _config_value(self.db, WA_API_TOKEN_OWNER_KEY)
        token_id = _config_value(self.db, WA_API_TOKEN_ID_KEY)
        if not owner_id or not token_id:
            return None
        try:
            owner = self.db.query(User).filter(User.id == int(owner_id)).first()
        except ValueError:
            return None
        if not owner:
            return None
        token = next((item for item in (owner.api_tokens or []) if isinstance(item, dict) and str(item.get("id")) == token_id), None)
        return token.get("token") if token else None

    def _latest_snapshot_with_file(self, camera_id: str) -> Optional[Snapshot]:
        snapshots = (
            self.db.query(Snapshot)
            .filter(Snapshot.camera_id == camera_id, Snapshot.deleted_at.is_(None))
            .order_by(Snapshot.timestamp.desc(), Snapshot.id.desc())
            .limit(100)
            .all()
        )
        return next(
            (
                item for item in snapshots
                if os.path.isfile(os.path.join("static", "snapshots", item.file_path))
            ),
            None,
        )

    def _snapshot_template_data(self, camera: Camera, snapshot: Snapshot) -> dict:
        """Build the snapshot object exposed to WhatsApp response templates."""
        health = self.db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
        camera_status = health.status if health else "Unknown"
        last_online = health.last_online if health else None
        if last_online and last_online.tzinfo is None:
            last_online = last_online.replace(tzinfo=timezone.utc)
        online = camera_status in {"Online", "High Latency"}
        uptime_seconds = (
            max(0, int((datetime.now(timezone.utc) - last_online).total_seconds()))
            if online and last_online else None
        )
        return {
            "filename": os.path.basename(snapshot.file_path),
            "camera": snapshot.camera_name or camera.hostname,
            "ip": snapshot.camera_ip or camera.ip or "",
            "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else "",
            "url": f"/snapshot/file/{snapshot.file_path}",
            "img_path": snapshot.file_path,
            "lat": camera.latitude if camera.latitude is not None else "",
            "long": camera.longitude if camera.longitude is not None else "",
            "camera_status": camera_status,
            "uptime": format_uptime_duration(uptime_seconds) if uptime_seconds is not None else "",
            "uptime_seconds": uptime_seconds,
            "last_online_at": last_online.isoformat() if last_online else "",
            "tamper_reason": snapshot.tamper_reason or "",
            "res": snapshot.resolution or "",
            "group_name": (", ".join(group.name for group in camera.groups) or (camera.group.name if camera.group else "")),
        }

    def _render_snap_stage_messages(
        self,
        messages: list[dict],
        api_context: dict,
        camera: Camera,
        argument: str,
        sender: str,
        last_snapshot: Optional[Snapshot] = None,
        error: str = "Snapshot capture failed",
    ) -> list[dict[str, str]]:
        timezone_name = get_current_timezone(self.db)
        rendered = []
        values = {
            "camera": camera.hostname,
            "ip": camera.ip or "",
            "argument": argument,
            "sender": sender,
            "error": error,
        }
        message_context = {**api_context}
        steps = dict(message_context.get("steps") or {})
        steps["result"] = {
            "status_code": 200 if last_snapshot else 404,
            "body": [self._snapshot_template_data(camera, last_snapshot)] if last_snapshot else [],
        }
        message_context["steps"] = steps
        if last_snapshot and steps["result"]["body"]:
            values.update(steps["result"]["body"][0])
        for item in messages:
            text = render_flow_template(str(item.get("text") or ""), message_context, timezone_name)
            text = render_response(text, **values)
            image_type = item.get("image_source_type", "none")
            outbound_item: dict[str, str] = {"text": text}
            if image_type in {"url", "api"}:
                image_url = render_flow_template(str(item.get("image_source") or ""), message_context, timezone_name)
                image_url = render_response(image_url, **values).strip()
                if image_type == "api":
                    api_path = unquote(image_url)
                    if api_path.startswith("/snapshot/file/"):
                        api_path = api_path.removeprefix("/snapshot/file/")
                    api_path = api_path.lstrip("/")
                    api_snapshot = (
                        self.db.query(Snapshot)
                        .filter(Snapshot.file_path == api_path, Snapshot.deleted_at.is_(None))
                        .first()
                    ) if api_path else None
                    if api_snapshot:
                        local_image_path = os.path.join("static", "snapshots", api_snapshot.file_path)
                        if os.path.isfile(local_image_path):
                            outbound_item["image_path"] = local_image_path
                parsed_url = urlsplit(image_url)
                if not outbound_item.get("image_path") and not parsed_url.scheme and not parsed_url.netloc:
                    public_base = (_config_value(self.db, "app_public_url") or "").strip()
                    if public_base:
                        image_url = urljoin(public_base.rstrip("/") + "/", image_url.lstrip("/"))
                        parsed_url = urlsplit(image_url)
                if outbound_item.get("image_path"):
                    pass
                elif parsed_url.scheme.lower() in {"http", "https"} and parsed_url.netloc:
                    outbound_item["image_url"] = image_url
                else:
                    logger.warning("Skipping invalid WhatsApp snapshot message image URL for camera %s", camera.hostname)
                    if not text:
                        outbound_item["text"] = "The configured image could not be loaded."
            elif image_type == "last_snapshot" and last_snapshot:
                outbound_item["image_path"] = os.path.join("static", "snapshots", last_snapshot.file_path)
            if outbound_item.get("text") or outbound_item.get("image_url") or outbound_item.get("image_path"):
                rendered.append(outbound_item)
        return rendered

    async def _handle_n8n_action(self, action: str, sender: str, argument: str, command: dict) -> str:
        """Execute the fixed native actions represented by n8n workflow commands."""
        template = command.get("response", "")
        if action in ("cctv", "snap"):
            if not argument:
                self.action_failed = True
                return f"Usage: {command['trigger']} hostname prefix, camera name, or IP address"
            if action == "cctv":
                hostname_prefix = argument.split(maxsplit=1)[0]
                snapshot_rows = self._find_latest_camera_snapshots_by_hostname_prefix(hostname_prefix)
                sent_items = []
                for matching_camera, snapshot in snapshot_rows:
                    local_path = os.path.join("static", "snapshots", snapshot.file_path)
                    if not os.path.isfile(local_path):
                        continue
                    caption = self._build_snapshot_caption(
                        matching_camera, snapshot, argument, sender, template
                    )
                    sent_items.append({"path": local_path, "caption": caption})
                    if len(sent_items) == 5:
                        break
                if not sent_items:
                    self.action_failed = True
                    return "No snapshot is available for the matching cameras."
                self.media_items = sent_items
                self.media_path = sent_items[0]["path"]
                self.media_caption = sent_items[0]["caption"]
                logger.info(
                    "WhatsApp CCTV prefix '%s' matched %s snapshots for sender_suffix=%s",
                    hostname_prefix, len(sent_items), self._sender[-4:],
                )
                return ""
            camera = (
                self._find_camera_for_capture(argument)
                if action == "snap"
                else self._find_camera(argument)
            )
            if not camera:
                if action == "snap":
                    self._set_action_error(f"Camera hostname or IP '{argument}' was not found or is inaccessible.", 404)
                    return f"Camera hostname or IP '{argument}' was not found or is inaccessible."
                self.action_failed = True
                return f"Camera '{argument}' was not found or is inaccessible."
            if action == "cctv":
                snapshot = (self.db.query(Snapshot).filter(Snapshot.camera_id == camera.id, Snapshot.deleted_at.is_(None)).order_by(Snapshot.timestamp.desc()).first())
            else:
                snapshot = None
            if action == "snap":
                health = self.db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
                camera_status = health.status if health else "Unknown"
                if camera_status not in {"Online", "High Latency"}:
                    self._set_action_error(f"Camera '{camera.hostname}' is {camera_status} (offline).", 503)
                    failure_messages = normalize_snap_stage_messages(
                        command.get("failure_messages"),
                        command.get("failure_response"),
                        "Camera is offline. Sending the latest saved snapshot if available.",
                        "last_snapshot",
                    )
                    previous_snapshot = self._latest_snapshot_with_file(camera.id)
                    self.outbound_items.extend(self._render_snap_stage_messages(
                        failure_messages,
                        {"sender": sender, "argument": argument, "steps": {}},
                        camera,
                        argument,
                        sender,
                        previous_snapshot,
                        error=f"{camera_status} (offline)",
                    ))
                    return ""
                api_context = {"sender": sender, "argument": argument, "steps": {},
                               "params": self.action_api_context.get("params", {})}
                if command.get("image_flow"):
                    try:
                        api_context = await run_api_flow(
                            flow=command["image_flow"],
                            bearer_token=self._selected_api_token(),
                            sender=sender,
                            argument=argument,
                            parameters=api_context.get("params", {}),
                        )
                    except Exception as exc:
                        if isinstance(exc, APIFlowError):
                            api_context = exc.context
                        logger.warning("WhatsApp snapshot image API flow failed for %s: %s", camera.hostname, exc)
                self.action_api_context = api_context
                # Keep result placeholders intact until the new capture is available.
                template = command.get("response", "")
                processing_messages = normalize_snap_stage_messages(
                    command.get("processing_messages"),
                    command.get("processing_response"),
                    "Snapshot capture for {{camera}} is in progress. Please wait.",
                )
                processing_snapshot = self._latest_snapshot_with_file(camera.id)
                processing_items = self._render_snap_stage_messages(
                    processing_messages, api_context, camera, argument, sender, processing_snapshot
                )
                await self._schedule_processing_items(
                    processing_items, command.get("processing_delay_seconds", 0)
                )
                snapshot = await SnapshotService.capture_snapshot(
                    camera.id, self.db, triggered_by="bot_builder_test" if self._is_builder_test else "whatsapp"
                )
                if snapshot and not os.path.isfile(os.path.join("static", "snapshots", snapshot.file_path)):
                    snapshot = None
                if not snapshot:
                    self._set_action_error(f"Failed to capture a new snapshot for '{camera.hostname}'.", 502)
                    await self._cancel_processing()
                    failure_messages = normalize_snap_stage_messages(
                        command.get("failure_messages"),
                        command.get("failure_response"),
                        "Could not capture a new snapshot for {{camera}}. Sending the latest available snapshot if one exists.",
                        "last_snapshot",
                    )
                    previous_snapshot = self._latest_snapshot_with_file(camera.id)
                    self.outbound_items.extend(self._render_snap_stage_messages(
                        failure_messages, api_context, camera, argument, sender, previous_snapshot
                    ))
                    return ""
            if not snapshot:
                self.action_failed = True
                return "No snapshot is available for this camera."
            snapshot_data = self._snapshot_template_data(camera, snapshot)
            context = self._merge_action_context({"argument": argument, "sender": sender, "steps": {"result": {"status_code": 200, "body": [snapshot_data]}}})
            local_path = os.path.join("static", "snapshots", snapshot.file_path)
            if not os.path.isfile(local_path):
                self.action_failed = True
                return "The snapshot file was not found on the server."
            self.media_path = local_path
            self.media_caption = self._render_action_response(
                template,
                context,
                timezone_name=get_current_timezone(self.db),
                camera=camera.hostname,
                ip=camera.ip or "",
                argument=argument,
                sender=sender,
            )
            self.action_api_context = context
            if action == "snap":
                self.outbound_items.append({"text": self.media_caption or "", "image_path": local_path})
            return ""
        if action == "ping":
            if not argument:
                self.action_failed = True
                return f"Usage: {command['trigger']} IP address or hostname"
            target = argument.split()[0]
            try:
                # Reuse the /ping endpoint implementation, off the event loop.
                result = await asyncio.to_thread(ping_ip, ip=target)
                replies = result.splitlines()
                online = any(reply.startswith("Reply from ") for reply in replies)
                failed = result.startswith("Ping failed for ")
                status_code = 502 if failed else (200 if online else 504)
                ping_context = self._merge_action_context({"steps": {"ping": {
                    "status_code": status_code,
                    "body": {"target": target, "replies": replies, "online": online, "result": result},
                }}})
                if status_code != 200:
                    self._set_action_error(result if failed else f"Host '{target}' did not respond to ping.", status_code)
                return self._render_action_response(
                    template, ping_context, timezone_name=get_current_timezone(self.db),
                    target=target, result=result, argument=argument, sender=sender,
                ) if template else result
            except Exception as exc:
                logger.warning("WhatsApp ping failed for %s: %s", target, exc)
                self.action_failed = True
                return f"Ping failed for {target}: {exc}"
        if action == "bot_status":
            connection = await run_gateway_blocking(WAGatewayService(self.db).check_connection)
            connected = bool(connection.get("connected"))
            logged_in = bool(connection.get("logged_in", connected))
            online = connected and logged_in
            response_time_ms = connection.get("response_time_ms")
            if not isinstance(response_time_ms, (int, float)) or isinstance(response_time_ms, bool):
                response_time_ms = None
            status = "Online" if online else "Offline"
            response_time = f"{int(response_time_ms)} ms" if response_time_ms is not None else "N/A"
            context = self._merge_action_context({"steps": {"result": {
                "status_code": 200 if online else 503,
                "body": {
                    "status": status,
                    "connected": connected,
                    "logged_in": logged_in,
                    "response_time_ms": response_time_ms,
                    "error": connection.get("error") or "",
                },
            }}})
            if not online:
                self._set_action_error(connection.get("error") or "WhatsApp gateway is offline.", 503)
            return self._render_action_response(
                template or "{{status_icon}} Bot Status: {{status}}\n🕒 Response Time: {{response_time}}",
                context,
                timezone_name=get_current_timezone(self.db),
                status=status,
                status_icon="✅" if online else "❌",
                response_time=response_time,
                response_time_ms=str(response_time_ms) if response_time_ms is not None else "N/A",
                argument=argument,
                sender=sender,
            )
        if action == "edit":
            match = re.match(r"([^,]+)", argument)
            hostname = match.group(1).strip() if match else ""
            camera = self.db.query(Camera).filter(Camera.hostname.ilike(hostname)).first()
            if not camera:
                self.action_failed = True
                return f"Camera '{hostname}' was not found."
            fields = {key: re.search(rf"{key}\s*:\s*([^,]+)", argument, re.I) for key in ("user", "pass", "ip", "port", "status", "group")}
            for key in ("user", "pass"):
                if not fields[key]:
                    self.action_failed = True
                    return f"The {key} parameter is required. Usage: /edit {hostname}, user: ..., pass: ..., ip: ..., port: ..., status: ..., group: ..."
            camera.username = fields["user"].group(1).strip()
            camera.password = fields["pass"].group(1).strip()
            if fields["ip"]:
                camera.ip = fields["ip"].group(1).strip()
            if fields["port"]:
                try:
                    camera.port = int(fields["port"].group(1).strip())
                except ValueError:
                    self.action_failed = True
                    return "Port must be a number."
            if fields["status"]:
                status = fields["status"].group(1).strip()
                if status not in {"Active", "Deactivated", "Maintenance", "Restricted", "Standalone"}:
                    self.action_failed = True
                    return "Invalid status. Use Active, Deactivated, Maintenance, Restricted, or Standalone."
                camera.status = status
            if fields["group"]:
                group_name = fields["group"].group(1).strip()
                group = self.db.query(CameraGroup).filter(CameraGroup.name.ilike(group_name)).first()
                if not group:
                    self.action_failed = True
                    return f"Group '{group_name}' was not found."
                camera.group_id = group.id
            self.db.flush()
            edit_context = self._merge_action_context({"steps": {"result": {
                "status_code": 200,
                "body": {"camera": camera.hostname, "hostname": camera.hostname, "ip": camera.ip or "",
                         "port": camera.port, "status": camera.status,
                         "group": camera.group.name if camera.group else ""},
            }}})
            return self._render_action_response(
                template, edit_context, timezone_name=get_current_timezone(self.db),
                camera=camera.hostname, ip=camera.ip or "", argument=argument, sender=sender,
            )
        if action == "token_check":
            rows = []
            token_rows = []
            for user in self.db.query(User).filter(User.api_tokens.isnot(None)).all():
                for token in user.api_tokens or []:
                    if isinstance(token, dict):
                        secret = str(token.get("token", ""))
                        masked = f"{secret[:5]}…{secret[-4:]}" if len(secret) > 10 else "(stored)"
                        rows.append(f"• {user.username}: {masked} · expires {token.get('expires_at') or '-'}")
                        token_rows.append({"username": user.username, "token": masked, "expires_at": token.get("expires_at") or "-"})
            token_context = self._merge_action_context({"steps": {"result": {
                "status_code": 200, "body": token_rows,
            }}})
            rendered_template = self._render_action_response(
                template, token_context, timezone_name=get_current_timezone(self.db),
                argument=argument, sender=sender,
            )
            if "{{#each steps.result.body}}" in command.get("response", ""):
                return rendered_template
            return (rendered_template + "\n" + "\n".join(rows)) if rows else "No API tokens found."
        if action in ("whitelist_add", "whitelist_remove"):
            parts = argument.split(maxsplit=1)
            if not parts:
                self.action_failed = True
                return "Invalid whitelist format. Use a number in the format 628xxxxxxxxxx."
            phone = re.sub(r"\D", "", parts[0])
            if phone.startswith("08"):
                phone = "628" + phone[1:]
            if not re.fullmatch(r"628[1-9]\d{7,12}", phone):
                self.action_failed = True
                return "Invalid phone number. Use the format 628xxxxxxxxxx."
            entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone).first()
            if action == "whitelist_add":
                if entry:
                    self.action_failed = True
                    return f"Number {phone} is already on the whitelist."
                name = parts[1].strip() if len(parts) > 1 else phone
                self.db.add(WhatsappWhitelist(phone_number=phone, name=name[:100], role=RoleEnum.user))
                self.db.flush()
            else:
                if phone == sender:
                    self.action_failed = True
                    return "You cannot remove your own number from the whitelist."
                if not entry:
                    self.action_failed = True
                    return f"Number {phone} was not found."
                self.db.delete(entry)
                self.db.flush()
            whitelist_context = self._merge_action_context({"steps": {"result": {
                "status_code": 200,
                "body": {"phone": phone, "action": "added" if action == "whitelist_add" else "removed", "success": True},
            }}})
            return self._render_action_response(
                template, whitelist_context, timezone_name=get_current_timezone(self.db),
                phone=phone, argument=argument, sender=sender,
            )
        self.action_failed = True
        return "This command is not supported."

    @staticmethod
    def _render_action_response(template: str, context: dict, timezone_name: str = "UTC", **legacy_values) -> str:
        """Render API JSON paths and preserve the legacy flat action placeholders."""
        rendered = render_flow_template(template, context, timezone_name=timezone_name)
        return render_response(rendered, **legacy_values)

    def _find_camera(self, query: str):
        entry = _whitelisted_sender(self.db, self._sender)
        camera_query = self.db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"]))
        if entry and entry.group_id:
            camera_query = camera_query.filter((Camera.group_id == entry.group_id) | Camera.groups.any(id=entry.group_id))
        return camera_query.filter((Camera.hostname.ilike(f"%{query}%")) | (Camera.ip == query)).order_by(Camera.hostname).first()

    def _find_camera_for_capture(self, hostname_or_ip: str) -> Optional[Camera]:
        """Find one accessible camera by exact hostname (case-insensitive) or exact IP."""
        entry = _whitelisted_sender(self.db, self._sender)
        lookup = hostname_or_ip.strip()
        escaped_hostname = lookup.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        camera_query = self.db.query(Camera).filter(
            Camera.status.in_(["Active", "Restricted", "Maintenance"]),
            (Camera.hostname.ilike(escaped_hostname, escape="\\")) | (Camera.ip == lookup),
        )
        if entry and entry.group_id:
            camera_query = camera_query.filter(
                (Camera.group_id == entry.group_id) | Camera.groups.any(id=entry.group_id)
            )
        return camera_query.order_by(Camera.hostname).first()

    def _find_latest_camera_snapshots_by_hostname_prefix(self, prefix: str) -> list[tuple[Camera, Snapshot]]:
        """Find each accessible matching camera's latest snapshot, newest cameras first."""
        entry = _whitelisted_sender(self.db, self._sender)
        escaped_prefix = prefix.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        latest_snapshots = (
            self.db.query(
                Snapshot.id.label("snapshot_id"),
                func.row_number().over(
                    partition_by=Snapshot.camera_id,
                    order_by=(Snapshot.timestamp.desc(), Snapshot.id.desc()),
                ).label("snapshot_rank"),
            )
            .filter(Snapshot.deleted_at.is_(None))
            .subquery()
        )
        query = (
            self.db.query(Camera, Snapshot)
            .join(Snapshot, Camera.id == Snapshot.camera_id)
            .join(latest_snapshots, latest_snapshots.c.snapshot_id == Snapshot.id)
            .filter(
                latest_snapshots.c.snapshot_rank == 1,
                Camera.hostname.ilike(f"{escaped_prefix}%", escape="\\"),
            )
        )
        if entry and entry.group_id:
            query = query.filter(
                (Camera.group_id == entry.group_id) | Camera.groups.any(id=entry.group_id)
            )
        return query.order_by(Camera.hostname).all()

    def _build_snapshot_caption(self, camera, snapshot, argument: str, sender: str, template: str) -> str:
        health = self.db.query(CameraHealth).filter(CameraHealth.camera_id == camera.id).first()
        camera_status = health.status if health else "Unknown"
        last_online = health.last_online if health else None
        if last_online and last_online.tzinfo is None:
            last_online = last_online.replace(tzinfo=timezone.utc)
        online = camera_status in {"Online", "High Latency"}
        uptime_seconds = (
            max(0, int((datetime.now(timezone.utc) - last_online).total_seconds()))
            if online and last_online else None
        )
        snapshot_data = {
            "filename": os.path.basename(snapshot.file_path),
            "camera": snapshot.camera_name,
            "ip": snapshot.camera_ip,
            "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else "",
            "url": f"/snapshot/file/{snapshot.file_path}",
            "img_path": snapshot.file_path,
            "lat": str(camera.latitude or None),
            "long": str(camera.longitude or None),
            "camera_status": camera_status,
            "uptime": format_uptime_duration(uptime_seconds) if uptime_seconds is not None else None,
            "uptime_seconds": uptime_seconds,
            "last_online_at": last_online.isoformat() if last_online else None,
            "tamper_reason": snapshot.tamper_reason,
            "res": snapshot.resolution,
            "group_name": (", ".join(group.name for group in camera.groups) or (camera.group.name if camera.group else "")),
        }
        context = self._merge_action_context({"argument": argument, "sender": sender, "steps": {"result": {"status_code": 200, "body": [snapshot_data]}}})
        caption = self._render_action_response(
            template, context, timezone_name=get_current_timezone(self.db),
            camera=camera.hostname, ip=camera.ip or "", argument=argument, sender=sender,
        )
        if not caption:
            return camera.hostname
        if camera.hostname.lower() not in caption.lower():
            return f"{camera.hostname}\n{caption}"
        return caption



@router.post("/webhook/gowa")
async def gowa_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Receive webhook from GoWA when incoming message arrives.
    
    GoWA payloads may be direct or nested under `payload`, for example
    {"payload": {"from": "6281234567890", "body": "/help", "chat_id": "..."}}.
    """
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            logger.warning("GoWA webhook received a non-object JSON payload")
            return json_success_response("Webhook payload ignored")

        raw_event = payload.get("payload")
        logger.info(
            "GoWA webhook received: event=%s top_level_fields=%s payload_fields=%s",
            payload.get("event", "legacy"),
            ",".join(sorted(payload.keys())),
            ",".join(sorted(raw_event.keys())) if isinstance(raw_event, dict) else "<not-object>",
        )

        if payload.get("event") not in (None, "message"):
            logger.info("Ignoring non-message GoWA event: %s", payload.get("event"))
            return json_success_response("Webhook event ignored")
        
        # GoWA payloads can arrive directly or nested under `payload`.
        event = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        if event.get("is_from_me") is True:
            logger.info("Ignoring outgoing GoWA message event")
            return json_success_response("Outgoing message ignored")
        sender = event.get("from", "")
        message = event.get("body") or event.get("message") or ""
        message_id = str(event.get("id") or event.get("message_id") or "").strip()
        
        if not sender or not message:
            logger.warning(
                "GoWA message missing sender or text: has_sender=%s has_body=%s",
                bool(sender),
                bool(message),
            )
            return json_success_response("Webhook received (empty)")
        
        # GoWA may provide a device-qualified JID such as
        # `628123456789:12@s.whatsapp.net`. Keep only the phone portion
        # before normalizing, matching the existing n8n workflow's ^\d+.
        sender = _normalize_wa_sender(sender)
        command, _ = WABotHandler(db).parse_command(message)
        logger.info(
            "GoWA message received: event=%s command=%s sender_suffix=%s",
            payload.get("event", "legacy"),
            command or "<empty>",
            sender[-4:] if sender else "<empty>",
        )

        # The webhook is public so GoWA can call it, so whitelist every bot
        # command here rather than relying on browser-session authentication.
        bot = WABotHandler(db)
        bot._sender = sender
        allowed = _whitelisted_sender(db, sender)
        if not allowed:
            logger.warning(
                "Ignoring WhatsApp command from non-whitelisted sender ending in %s",
                sender[-4:] if sender else "<empty>",
            )
            return json_success_response("Sender is not allowed")

        # Store the provider ID in its own unique field so GoWA retries remain
        # idempotent without polluting command analytics with synthetic markers.
        inbound_record = WhatsAppMessageLog(
            phone_number=sender[:32],
            provider_message_id=message_id[:200] or None,
            direction="inbound",
            status="received",
            message=message[:4000],
            command=command[:120] or None,
        )
        if message_id:
            duplicate = db.query(WhatsAppMessageLog.id).filter(
                WhatsAppMessageLog.provider_message_id == message_id[:200]
            ).first()
            if duplicate:
                logger.info("Ignoring duplicate GoWA event id=%s sender_suffix=%s", message_id, sender[-4:])
                return json_success_response("Duplicate webhook event ignored")
        db.add(inbound_record)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.info("Ignoring concurrent duplicate GoWA event sender_suffix=%s", sender[-4:])
            return json_success_response("Duplicate webhook event ignored")

        matched_command, _ = match_command(message, get_command_settings(db))
        chat_id = str(event.get("chat_id") or sender).strip()
        is_group = bool(event.get("is_group")) or chat_id.lower().endswith("@g.us")
        command_allowed_in_chat = not is_group or not matched_command or matched_command.get("chat_scope") != "private"
        if matched_command and command_allowed_in_chat:
            db.add(CommandLog(
                user_id=sender,
                command=_analytics_command_text(message),
                source="whatsapp",
            ))
            db.commit()
        # Handle command
        wa_service = WAGatewayService(db)

        async def send_snapshot_progress(message_item: dict[str, str]) -> dict:
            progress_recipient = sender if bot.private_response else chat_id
            progress_reply_to = (message_id or None) if bot.quote_reply else None
            result = await run_gateway_blocking(
                _send_wa_progress_item, progress_recipient, message_item, sender, command, progress_reply_to
            )
            return result

        bot.progress_sender = send_snapshot_progress
        # GoWA documents chat_id as the target chat JID. This also preserves a
        # group JID when the command was sent in a group chat.
        response = await bot.handle(sender, message, is_group=is_group)
        # API workflows can return protected system data; keep those replies in
        # the invoking admin's direct chat even when the trigger came from group chat.
        recipient = sender if bot.private_response else chat_id
        reply_to = (message_id or None) if bot.quote_reply and not bot.private_response else None
        
        # Send response back
        if bot.outbound_items:
            failed_sends = []
            for item in bot.outbound_items:
                send_result = await run_gateway_blocking(
                    _send_wa_message_item, wa_service, recipient, item, reply_to
                )
                if not send_result.get("success"):
                    failed_sends.append(send_result.get("error", "unknown error"))
                    logger.error("Failed sending WhatsApp message to %s: %s", recipient, send_result.get("error"))
                _record_wa_message(
                    db,
                    sender,
                    "outbound",
                    "accepted" if send_result.get("success") else "failed",
                    item.get("text") or ("[video]" if item.get("video_path") else "[image]"),
                    command,
                    send_result.get("error", ""),
                )
            if failed_sends:
                failure_text = "Some media messages could not be sent. Please try again."
                fallback_send = await run_gateway_blocking(
                    wa_service.send_text, recipient, failure_text, reply_to
                )
                _record_wa_message(
                    db, sender, "outbound", "accepted" if fallback_send.get("success") else "failed",
                    failure_text, command, fallback_send.get("error", ""),
                )
        elif bot.media_path:
            media_items = bot.media_items or [{"path": bot.media_path, "caption": bot.media_caption or ""}]
            failed_sends = []
            for index, media_item in enumerate(media_items):
                send_result = await run_gateway_blocking(
                    _send_wa_message_item,
                    wa_service,
                    recipient,
                    {"image_path": media_item["path"], "text": media_item.get("caption") or ""},
                    reply_to if index == 0 else None,
                )
                if not send_result.get("success"):
                    failed_sends.append(send_result.get("error", "unknown error"))
                    logger.error("Failed sending WhatsApp image to %s: %s", recipient, send_result.get("error"))
                _record_wa_message(
                    db, sender, "outbound", "accepted" if send_result.get("success") else "failed",
                    media_item.get("caption") or "[image]", command, send_result.get("error", ""),
                )
            if failed_sends:
                failure_text = (
                    "Some snapshots could not be sent. Please try again."
                    if len(media_items) > 1 else
                    "The snapshot was captured, but the image could not be sent. Please try again."
                )
                fallback_send = await run_gateway_blocking(
                    wa_service.send_text, recipient, failure_text, reply_to
                )
                _record_wa_message(
                    db, sender, "outbound", "accepted" if fallback_send.get("success") else "failed",
                    failure_text, command, fallback_send.get("error", ""),
                )
        elif response:
            send_result = await run_gateway_blocking(
                wa_service.send_text, recipient, response, reply_to
            )
            if not send_result.get("success"):
                logger.error(
                    "Failed to send WhatsApp /help response to %s: %s",
                    recipient,
                    send_result.get("error", "unknown GoWA error"),
                )
            _record_wa_message(db, sender, "outbound", "accepted" if send_result.get("success") else "failed", response, command, send_result.get("error", ""))
        db.commit()
        
        return json_success_response("Message processed")
        
    except Exception as e:
        logger.error(f"Error processing GoWA webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/wa/status")
def get_wa_status(
    settings: GoWATestConfig,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Check GoWA using settings from the form without saving them."""
    service = WAGatewayService(db)
    service.config._cache["gowa_enabled"] = "1" if settings.enabled else "0"
    service.config._cache["gowa_base_url"] = settings.base_url.strip().rstrip("/")
    service.config._cache["gowa_api_key"] = settings.api_key.strip()
    service.config._cache["gowa_device_id"] = settings.device_id.strip()
    service.session.headers.pop("Authorization", None)
    service.session.headers.pop("X-Device-Id", None)
    if settings.api_key.strip():
        service.session.headers.update(WAGatewayService._auth_headers(settings.api_key.strip()))
    if settings.device_id.strip():
        service.session.headers["X-Device-Id"] = settings.device_id.strip()
    status = service.check_connection()
    
    if status["connected"]:
        return json_success_response("WhatsApp connected", status)
    else:
        return json_error_response(status.get("error", "Not connected"), 503)


@router.get("/api/wa/groups")
def get_wa_groups(
    enabled: Optional[bool] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """List WhatsApp groups from GoWA for receiver selection."""
    service = WAGatewayService(db)
    if enabled is not None:
        service.config._cache["gowa_enabled"] = "1" if enabled else "0"
    if base_url is not None:
        service.config._cache["gowa_base_url"] = base_url.strip().rstrip("/")
    if api_key is not None:
        service.config._cache["gowa_api_key"] = api_key.strip()
        service.session.headers.pop("Authorization", None)
        if api_key.strip():
            service.session.headers.update(WAGatewayService._auth_headers(api_key.strip()))

    result = service.list_groups()
    if result["success"]:
        return json_success_response("Groups loaded", {"groups": result.get("groups", [])})
    return json_error_response(result.get("error", "Failed to load groups"), 502)


@router.post("/api/wa/send-test")
def send_test_wa(
    phone: str,
    message: str = "🤖 Test message from B-SNAP",
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required)
):
    """Send test WhatsApp message."""
    service = WAGatewayService(db)
    phone = format_phone_number(phone)
    
    result = service.send_text(phone, message)
    
    if result["success"]:
        return json_success_response("Message sent", result)
    else:
        return json_error_response(result.get("error", "Failed to send"), 500)
