"""
GoWA Webhook Handler - WhatsApp Bot Integration
Handles incoming messages from GoWA webhook and responds to commands.
"""
import logging
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import get_db
from app.models.user import User
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.utils.response_helper import json_success_response, json_error_response
from app.utils.wa_bot_commands import get_command_settings, match_command, render_response, save_command_settings
from app.models.camera_group import CameraGroup
from app.models.snapshot import Snapshot
from app.models.log import CommandLog
from app.models.config import Configuration
from app.utils.snapshot_service import SnapshotService
from app.utils.wa_bot_workflow import render_flow_template, run_api_flow
from app.utils.wa_bot_workflow import normalize_flow
from app.utils.healthcheck import format_uptime_duration
import os

logger = logging.getLogger("main")

router = APIRouter(tags=["WhatsApp"])
WA_API_TOKEN_OWNER_KEY = "wa_bot_api_token_owner_id"
WA_API_TOKEN_ID_KEY = "wa_bot_api_token_id"


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
            raise ValueError("Token API harus dipilih dari token milik admin yang sedang login")
        _set_config_value(db, WA_API_TOKEN_OWNER_KEY, str(current_admin.id) if token_id else "")
        _set_config_value(db, WA_API_TOKEN_ID_KEY, token_id)
        db.commit()
        return {"status": "success", "commands": commands}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))


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
                raise ValueError("API token tidak ditemukan pada akun admin yang sedang login")
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
        detail = str(exc) or "API flow gagal dijalankan"
        raise HTTPException(status_code=502, detail=detail[:500]) from exc


class WABotHandler:
    """Handler for WhatsApp bot commands."""
    
    def __init__(self, db: Session):
        self.db = db
        self.media_path: Optional[str] = None
        self.media_caption: Optional[str] = None
        self.private_response = False
        self.quote_reply = True
    
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
    
    async def handle(self, sender: str, message: str) -> str:
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
            self.quote_reply = bool(command.get("quote_reply", True))
            entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == sender).first()
            is_admin = bool(entry and entry.role == RoleEnum.admin)
            if command.get("role") == "admin" and not is_admin:
                return "Perintah ini hanya tersedia untuk admin."
            required_params = command.get("required_params") or []
            argument_count = len(argument.split())
            missing_required_params = bool(required_params) and (
                not argument.strip()
                or (len(required_params) > 1 and argument_count < len(required_params))
            )
            if missing_required_params:
                usage = " ".join(f"<{parameter}>" for parameter in required_params)
                return f"Parameter belum lengkap. Format: {command['trigger']} {usage}"
            action = command.get("action")
            if action == "help":
                enabled = [c for c in commands if c.get("enabled") and (c.get("role") != "admin" or is_admin)]
                heading = render_response(command.get("response", "*Perintah B-Snap Bot*"), argument=argument, sender=sender)
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
                    return render_flow_template(command.get("response", ""), context)
                except Exception as exc:
                    logger.warning("WhatsApp B-Snap API flow failed for command %s: %s", command.get("trigger"), exc)
                    return f"Flow API gagal: {exc}"
            return await self._handle_n8n_action(action, sender, argument, command)

        for configured in commands:
            if configured.get("enabled"):
                continue
            for phrase in [configured.get("trigger", ""), *configured.get("aliases", [])]:
                phrase = str(phrase).strip().lower()
                if phrase and (message.strip().lower() == phrase or message.strip().lower().startswith(phrase + " ")):
                    return "Perintah ini sedang dinonaktifkan."

        if message.lstrip().startswith(("/", "!")):
            logger.info("WA Bot: %s sent an unconfigured command", sender[-4:])
            return "Command belum dikonfigurasi di WhatsApp Bot Builder."
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

    async def _handle_n8n_action(self, action: str, sender: str, argument: str, command: dict) -> str:
        """Execute the fixed native actions represented by n8n workflow commands."""
        template = command.get("response", "")
        if action in ("cctv", "snap"):
            if not argument:
                return f"Format: {command['trigger']} nama kamera atau IP"
            camera = self._find_camera(argument)
            if not camera:
                return f"Kamera '{argument}' tidak ditemukan atau tidak dapat diakses."
            if action == "cctv":
                snapshot = (self.db.query(Snapshot).filter(Snapshot.camera_id == camera.id, Snapshot.deleted_at.is_(None)).order_by(Snapshot.timestamp.desc()).first())
            else:
                snapshot = None
            if action == "snap":
                snapshot = await SnapshotService.capture_snapshot(camera.id, self.db, triggered_by="whatsapp")
            if not snapshot:
                return "Snapshot tidak tersedia untuk kamera tersebut."
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
                return "File snapshot tidak ditemukan di server."
            self.media_path = local_path
            self.media_caption = self._render_action_response(template, context, camera=camera.hostname, ip=camera.ip or "", argument=argument, sender=sender)
            return ""
        if action == "ping":
            if not argument:
                return "Format: /ping alamat IP atau hostname"
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
                return f"Kamera '{hostname}' tidak ditemukan."
            fields = {key: re.search(rf"{key}\s*:\s*([^,]+)", argument, re.I) for key in ("user", "pass", "ip", "port", "status", "group")}
            for key in ("user", "pass"):
                if not fields[key]:
                    return f"Parameter {key} wajib. Format: /edit {hostname}, user: ..., pass: ..., ip: ..., port: ..., status: ..., group: ..."
            camera.username = fields["user"].group(1).strip()
            camera.password = fields["pass"].group(1).strip()
            if fields["ip"]:
                camera.ip = fields["ip"].group(1).strip()
            if fields["port"]:
                try:
                    camera.port = int(fields["port"].group(1).strip())
                except ValueError:
                    return "Port harus berupa angka."
            if fields["status"]:
                status = fields["status"].group(1).strip()
                if status not in {"Active", "Deactivated", "Maintenance", "Restricted", "Standalone"}:
                    return "Status tidak valid. Gunakan Active, Deactivated, Maintenance, Restricted, atau Standalone."
                camera.status = status
            if fields["group"]:
                group_name = fields["group"].group(1).strip()
                group = self.db.query(CameraGroup).filter(CameraGroup.name.ilike(group_name)).first()
                if not group:
                    return f"Grup '{group_name}' tidak ditemukan."
                camera.group_id = group.id
            self.db.flush()
            return render_response(template, camera=camera.hostname, argument=argument, sender=sender)
        if action == "token_check":
            rows = []
            for user in self.db.query(User).filter(User.api_tokens.isnot(None)).all():
                for token in user.api_tokens or []:
                    if isinstance(token, dict):
                        secret = str(token.get("token", ""))
                        masked = f"{secret[:5]}…{secret[-4:]}" if len(secret) > 10 else "(tersimpan)"
                        rows.append(f"• {user.username}: {masked} · expires {token.get('expires_at') or '-'}")
            rendered_template = render_response(template, argument=argument, sender=sender)
            return (rendered_template + "\n" + "\n".join(rows)) if rows else "Tidak ada token API."
        if action in ("whitelist_add", "whitelist_remove"):
            parts = argument.split(maxsplit=1)
            if not parts:
                return "Format whitelist tidak valid. Gunakan nomor 628xxxxxxxxxx."
            phone = re.sub(r"\D", "", parts[0])
            if phone.startswith("08"):
                phone = "628" + phone[1:]
            if not re.fullmatch(r"628[1-9]\d{7,12}", phone):
                return "Nomor tidak valid. Gunakan format 628xxxxxxxxxx."
            entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == phone).first()
            if action == "whitelist_add":
                if entry:
                    return f"Nomor {phone} sudah ada di whitelist."
                name = parts[1].strip() if len(parts) > 1 else phone
                self.db.add(WhatsappWhitelist(phone_number=phone, name=name[:100], role=RoleEnum.user))
                self.db.flush()
            else:
                if phone == sender:
                    return "Anda tidak dapat menghapus nomor sendiri dari whitelist."
                if not entry:
                    return f"Nomor {phone} tidak ditemukan."
                self.db.delete(entry)
                self.db.flush()
            return render_response(template, phone=phone, argument=argument, sender=sender)
        return "Perintah belum didukung."

    @staticmethod
    def _render_action_response(template: str, context: dict, **legacy_values) -> str:
        """Render API JSON paths and preserve the legacy flat action placeholders."""
        rendered = render_flow_template(template, context)
        return render_response(rendered, **legacy_values)

    def _find_camera(self, query: str):
        entry = self.db.query(WhatsappWhitelist).filter(WhatsappWhitelist.phone_number == self._sender).first()
        camera_query = self.db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"]))
        if entry and entry.group_id:
            camera_query = camera_query.filter((Camera.group_id == entry.group_id) | Camera.groups.any(id=entry.group_id))
        return camera_query.filter((Camera.hostname.ilike(f"%{query}%")) | (Camera.ip == query)).order_by(Camera.hostname).first()



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

        # GoWA may redeliver an inbound event if webhook processing takes too long
        # or the response is lost. Persist the event marker before a snapshot so a
        # retry cannot create the same snapshot multiple times.
        event_marker = f"gowa_event:{message_id[:180]}" if message_id else None
        if event_marker:
            duplicate = db.query(CommandLog.id).filter(
                CommandLog.user_id == sender,
                CommandLog.command == event_marker,
                CommandLog.source == "whatsapp_webhook",
            ).first()
            if duplicate:
                logger.info("Ignoring duplicate GoWA event id=%s sender_suffix=%s", message_id, sender[-4:])
                return json_success_response("Duplicate webhook event ignored")
            db.add(CommandLog(user_id=sender, command=event_marker, source="whatsapp_webhook"))
            db.commit()

        # Handle command
        wa_service = WAGatewayService(db)
        # GoWA documents chat_id as the target chat JID. This also preserves a
        # group JID when the command was sent in a group chat.
        response = await bot.handle(sender, message)
        # API workflows can return protected system data; keep those replies in
        # the invoking admin's direct chat even when the trigger came from group chat.
        recipient = sender if bot.private_response else str(event.get("chat_id") or sender).strip()
        reply_to = (message_id or None) if bot.quote_reply and not bot.private_response else None
        
        # Send response back
        if bot.media_path:
            send_result = wa_service.send_image_file(
                recipient,
                bot.media_path,
                bot.media_caption,
                reply_to=reply_to,
            )
            if not send_result.get("success"):
                logger.error("Failed sending WhatsApp image to %s: %s", recipient, send_result.get("error"))
                wa_service.send_text(
                    recipient,
                    "Snapshot berhasil diambil, tetapi gambar gagal dikirim. Silakan coba lagi.",
                    reply_to=reply_to,
                )
        elif response:
            send_result = wa_service.send_text(recipient, response, reply_to=reply_to)
            if not send_result.get("success"):
                logger.error(
                    "Failed to send WhatsApp /help response to %s: %s",
                    recipient,
                    send_result.get("error", "unknown GoWA error"),
                )
        
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
