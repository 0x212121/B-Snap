"""
GoWA Webhook Handler - WhatsApp Bot Integration
Handles incoming messages from GoWA webhook and responds to commands.
"""
import logging
import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlsplit
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Callable, Optional

from app.db.database import get_db
from app.models.user import User
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.utils.response_helper import json_success_response, json_error_response
from app.utils.wa_bot_commands import (
    get_command_settings,
    match_command,
    normalize_snap_stage_messages,
    render_response,
    save_command_settings,
)
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.log import CommandLog, WhatsAppMessageLog
from app.models.config import Configuration
from app.utils.snapshot_service import SnapshotService
from app.utils.wa_bot_workflow import render_flow_template, run_api_flow
from app.utils.wa_bot_workflow import normalize_flow
from app.utils.healthcheck import format_uptime_duration
from app.utils.timezone_helper import get_current_timezone
import os

logger = logging.getLogger("main")

router = APIRouter(tags=["WhatsApp"])
WA_API_TOKEN_OWNER_KEY = "wa_bot_api_token_owner_id"
WA_API_TOKEN_ID_KEY = "wa_bot_api_token_id"


def _record_wa_message(db: Session, phone: str, direction: str, status: str, message: str = "", command: str = "", error: str = "", provider_message_id: str | None = None) -> None:
    db.add(WhatsAppMessageLog(phone_number=phone[:32], provider_message_id=provider_message_id,
                              direction=direction, status=status,
                              message=message[:4000] or None, command=command[:120] or None,
                              error=error[:1000] or None))


def _send_wa_message_item(service: WAGatewayService, recipient: str, item: dict, reply_to: str | None = None) -> dict:
    caption = str(item.get("text") or "")
    if item.get("image_path"):
        return service.send_image_file(recipient, item["image_path"], caption or None, reply_to=reply_to)
    if item.get("image_url"):
        return service.send_image(recipient, item["image_url"], caption or None, reply_to=reply_to)
    if caption:
        return service.send_text(recipient, caption, reply_to=reply_to)
    return {"success": False, "error": "Message item has no text or image"}


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
async def test_wa_bot_flow(request: Request, current_admin: User = Depends(admin_access_required)):
    """Run an unsaved WhatsApp API flow once for the builder preview."""
    try:
        payload = await request.json()
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
            sender=str(payload.get("sender") or "").strip()[:100],
            argument=str(payload.get("argument") or "").strip()[:500],
        )
        return {"status": "success", "steps": context["steps"]}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("WhatsApp Bot API flow test failed for admin %s: %s", current_admin.id, exc)
        detail = str(exc) or "The API flow could not be run"
        raise HTTPException(status_code=502, detail=detail[:500]) from exc


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
        self.progress_sender: Optional[Callable[[dict[str, str]], dict]] = None
    
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
        """
        Handle incoming message and return response.
        
        Args:
            sender: Sender phone number
            message: Message text
        
        Returns:
            Response message
        """
        self._sender = sender
        commands = get_command_settings(self.db)
        command, argument = match_command(message, commands)
        if command:
            if is_group and command.get("chat_scope") == "private":
                logger.info("Ignoring private-only WhatsApp command in group: %s", command.get("trigger"))
                return ""
            self.quote_reply = bool(command.get("quote_reply", True))
            entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == sender).first()
            is_admin = bool(entry and entry.role == RoleEnum.admin)
            if command.get("role") == "admin" and not is_admin:
                return "This command is available to admins only."
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
            if action == "help":
                enabled = [
                    c for c in commands
                    if c.get("enabled")
                    and (c.get("role") != "admin" or is_admin)
                    and (not is_group or c.get("chat_scope") != "private")
                ]
                heading = render_response(command.get("response", "*B-Snap Bot Commands*"), argument=argument, sender=sender)
                return heading + "\n" + "\n".join(
                    f"{c['trigger']}"
                    + (" " + " ".join(f"<{parameter}>" for parameter in c.get("required_params", [])) if c.get("required_params") else "")
                    + f" - {c.get('description') or c['name']}"
                    for c in enabled
                )
            if action == "ping_test":
                return render_response(command.get("response", "Pong"), argument=argument, sender=sender)
            if action == "text":
                return render_response(command.get("response", ""), argument=argument, sender=sender)
            if action == "api_flow":
                self.private_response = True
                try:
                    token = self._selected_api_token()
                    context = await run_api_flow(
                        flow=command["flow"],
                        bearer_token=token,
                        sender=sender,
                        argument=argument,
                    )
                    return render_flow_template(
                        command.get("response", ""),
                        context,
                        timezone_name=get_current_timezone(self.db),
                    )
                except Exception as exc:
                    logger.warning("WhatsApp B-Snap API flow failed for command %s: %s", command.get("trigger"), exc)
                    return f"API flow failed: {exc}"
            return await self._handle_n8n_action(action, sender, argument, command)

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

    def _render_snap_stage_messages(
        self,
        messages: list[dict],
        api_context: dict,
        camera: Camera,
        argument: str,
        sender: str,
        last_snapshot: Optional[Snapshot] = None,
    ) -> list[dict[str, str]]:
        timezone_name = get_current_timezone(self.db)
        rendered = []
        values = {
            "camera": camera.hostname,
            "ip": camera.ip or "",
            "argument": argument,
            "sender": sender,
            "error": "Snapshot capture failed",
        }
        for item in messages:
            text = render_flow_template(str(item.get("text") or ""), api_context, timezone_name)
            text = render_response(text, **values)
            image_type = item.get("image_source_type", "none")
            outbound_item: dict[str, str] = {"text": text}
            if image_type in {"url", "api"}:
                image_url = render_flow_template(str(item.get("image_source") or ""), api_context, timezone_name)
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
                    return f"Camera hostname or IP '{argument}' was not found or is inaccessible."
                return f"Camera '{argument}' was not found or is inaccessible."
            if action == "cctv":
                snapshot = (self.db.query(Snapshot).filter(Snapshot.camera_id == camera.id, Snapshot.deleted_at.is_(None)).order_by(Snapshot.timestamp.desc()).first())
            else:
                snapshot = None
            if action == "snap":
                api_context = {"sender": sender, "argument": argument, "steps": {}}
                if command.get("image_flow"):
                    try:
                        api_context = await run_api_flow(
                            flow=command["image_flow"],
                            bearer_token=self._selected_api_token(),
                            sender=sender,
                            argument=argument,
                        )
                    except Exception as exc:
                        logger.warning("WhatsApp snapshot image API flow failed for %s: %s", camera.hostname, exc)
                processing_messages = normalize_snap_stage_messages(
                    command.get("processing_messages"),
                    command.get("processing_response"),
                    "Snapshot capture for {{camera}} is in progress. Please wait.",
                )
                processing_snapshot = self._latest_snapshot_with_file(camera.id)
                processing_items = self._render_snap_stage_messages(
                    processing_messages, api_context, camera, argument, sender, processing_snapshot
                )
                for processing_item in processing_items:
                    if self.progress_sender:
                        try:
                            progress_result = self.progress_sender(processing_item)
                            if not progress_result.get("success"):
                                logger.warning(
                                    "Failed to send WhatsApp snapshot processing message for %s: %s",
                                    camera.hostname, progress_result.get("error", "unknown error"),
                                )
                        except Exception:
                            logger.exception("Failed to send WhatsApp snapshot processing message for %s", camera.hostname)
                    else:
                        self.outbound_items.append(processing_item)
                snapshot = await SnapshotService.capture_snapshot(camera.id, self.db, triggered_by="whatsapp")
                if snapshot and not os.path.isfile(os.path.join("static", "snapshots", snapshot.file_path)):
                    snapshot = None
                if not snapshot:
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
                return "No snapshot is available for this camera."
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
            context = {"argument": argument, "sender": sender, "steps": {"result": {"status_code": 200, "body": [snapshot_data]}}}
            local_path = os.path.join("static", "snapshots", snapshot.file_path)
            if not os.path.isfile(local_path):
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
            if action == "snap":
                self.outbound_items.append({"text": self.media_caption or "", "image_path": local_path})
            return ""
        if action == "ping":
            if not argument:
                return "Usage: /ping IP address or hostname"
            target = argument.split()[0]
            try:
                import ping3
                replies = []
                for attempt in range(4):
                    latency = ping3.ping(target, timeout=2)
                    if latency is None:
                        replies.append(f"Request timeout for attempt {attempt + 1}")
                    else:
                        replies.append(f"Reply from {target}: time={latency * 1000:.2f}ms")
                return "\n".join(replies)
            except Exception as exc:
                logger.warning("WhatsApp ping failed for %s: %s", target, exc)
                return f"Ping failed for {target}: {exc}"
        if action == "edit":
            match = re.match(r"([^,]+)", argument)
            hostname = match.group(1).strip() if match else ""
            camera = self.db.query(Camera).filter(Camera.hostname.ilike(hostname)).first()
            if not camera:
                return f"Camera '{hostname}' was not found."
            fields = {key: re.search(rf"{key}\s*:\s*([^,]+)", argument, re.I) for key in ("user", "pass", "ip", "port", "status", "group")}
            for key in ("user", "pass"):
                if not fields[key]:
                    return f"The {key} parameter is required. Usage: /edit {hostname}, user: ..., pass: ..., ip: ..., port: ..., status: ..., group: ..."
            camera.username = fields["user"].group(1).strip()
            camera.password = fields["pass"].group(1).strip()
            if fields["ip"]:
                camera.ip = fields["ip"].group(1).strip()
            if fields["port"]:
                try:
                    camera.port = int(fields["port"].group(1).strip())
                except ValueError:
                    return "Port must be a number."
            if fields["status"]:
                status = fields["status"].group(1).strip()
                if status not in {"Active", "Deactivated", "Maintenance", "Restricted", "Standalone"}:
                    return "Invalid status. Use Active, Deactivated, Maintenance, Restricted, or Standalone."
                camera.status = status
            if fields["group"]:
                group_name = fields["group"].group(1).strip()
                group = self.db.query(CameraGroup).filter(CameraGroup.name.ilike(group_name)).first()
                if not group:
                    return f"Group '{group_name}' was not found."
                camera.group_id = group.id
            self.db.flush()
            return render_response(template, camera=camera.hostname, argument=argument, sender=sender)
        if action == "token_check":
            rows = []
            for user in self.db.query(User).filter(User.api_tokens.isnot(None)).all():
                for token in user.api_tokens or []:
                    if isinstance(token, dict):
                        secret = str(token.get("token", ""))
                        masked = f"{secret[:5]}…{secret[-4:]}" if len(secret) > 10 else "(stored)"
                        rows.append(f"• {user.username}: {masked} · expires {token.get('expires_at') or '-'}")
            rendered_template = render_response(template, argument=argument, sender=sender)
            return (rendered_template + "\n" + "\n".join(rows)) if rows else "No API tokens found."
        if action in ("whitelist_add", "whitelist_remove"):
            parts = argument.split(maxsplit=1)
            if not parts:
                return "Invalid whitelist format. Use a number in the format 628xxxxxxxxxx."
            phone = re.sub(r"\D", "", parts[0])
            if phone.startswith("08"):
                phone = "628" + phone[1:]
            if not re.fullmatch(r"628[1-9]\d{7,12}", phone):
                return "Invalid phone number. Use the format 628xxxxxxxxxx."
            entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone).first()
            if action == "whitelist_add":
                if entry:
                    return f"Number {phone} is already on the whitelist."
                name = parts[1].strip() if len(parts) > 1 else phone
                self.db.add(WhatsappWhitelist(phone_number=phone, name=name[:100], role=RoleEnum.user))
                self.db.flush()
            else:
                if phone == sender:
                    return "You cannot remove your own number from the whitelist."
                if not entry:
                    return f"Number {phone} was not found."
                self.db.delete(entry)
                self.db.flush()
            return render_response(template, phone=phone, argument=argument, sender=sender)
        return "This command is not supported."

    @staticmethod
    def _render_action_response(template: str, context: dict, timezone_name: str = "UTC", **legacy_values) -> str:
        """Render API JSON paths and preserve the legacy flat action placeholders."""
        rendered = render_flow_template(template, context, timezone_name=timezone_name)
        return render_response(rendered, **legacy_values)

    def _find_camera(self, query: str):
        entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == self._sender).first()
        camera_query = self.db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"]))
        if entry and entry.group_id:
            camera_query = camera_query.filter((Camera.group_id == entry.group_id) | Camera.groups.any(id=entry.group_id))
        return camera_query.filter((Camera.hostname.ilike(f"%{query}%")) | (Camera.ip == query)).order_by(Camera.hostname).first()

    def _find_camera_for_capture(self, hostname_or_ip: str) -> Optional[Camera]:
        """Find one accessible camera by exact hostname (case-insensitive) or exact IP."""
        entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == self._sender).first()
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
        entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == self._sender).first()
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
        context = {"argument": argument, "sender": sender, "steps": {"result": {"status_code": 200, "body": [snapshot_data]}}}
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
        sender_phone = re.split(r"[@:]", str(sender), maxsplit=1)[0]
        sender = format_phone_number(sender_phone)
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
        allowed = (
            db.query(WhatsappWhitelist)
            .filter(WhatsappWhitelist.phone_number == sender)
            .first()
        )
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

        def send_snapshot_progress(message_item: dict[str, str]) -> dict:
            progress_reply_to = (message_id or None) if bot.quote_reply else None
            result = _send_wa_message_item(wa_service, chat_id, message_item, reply_to=progress_reply_to)
            _record_wa_message(
                db,
                sender,
                "outbound",
                "accepted" if result.get("success") else "failed",
                message_item.get("text") or "[image]",
                command,
                result.get("error", ""),
            )
            db.commit()
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
                send_result = _send_wa_message_item(wa_service, recipient, item, reply_to=reply_to)
                if not send_result.get("success"):
                    failed_sends.append(send_result.get("error", "unknown error"))
                    logger.error("Failed sending WhatsApp snapshot message to %s: %s", recipient, send_result.get("error"))
                _record_wa_message(
                    db,
                    sender,
                    "outbound",
                    "accepted" if send_result.get("success") else "failed",
                    item.get("text") or "[image]",
                    command,
                    send_result.get("error", ""),
                )
            if failed_sends:
                failure_text = "Some snapshot messages could not be sent. Please try again."
                fallback_send = wa_service.send_text(recipient, failure_text, reply_to=reply_to)
                _record_wa_message(
                    db, sender, "outbound", "accepted" if fallback_send.get("success") else "failed",
                    failure_text, command, fallback_send.get("error", ""),
                )
        elif bot.media_path:
            media_items = bot.media_items or [{"path": bot.media_path, "caption": bot.media_caption or ""}]
            failed_sends = []
            for index, media_item in enumerate(media_items):
                send_result = wa_service.send_image_file(
                    recipient,
                    media_item["path"],
                    media_item.get("caption"),
                    reply_to=reply_to if index == 0 else None,
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
                fallback_send = wa_service.send_text(recipient, failure_text, reply_to=reply_to)
                _record_wa_message(
                    db, sender, "outbound", "accepted" if fallback_send.get("success") else "failed",
                    failure_text, command, fallback_send.get("error", ""),
                )
        elif response:
            send_result = wa_service.send_text(recipient, response, reply_to=reply_to)
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
