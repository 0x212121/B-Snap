"""
GoWA Webhook Handler - WhatsApp Bot Integration
Handles incoming messages from GoWA webhook and responds to commands.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
import pytz
from app.utils.timezone_helper import get_current_timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from app.db.database import get_db
from app.models.user import User
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.camera_daily_stats import CameraDailyStats
from app.models.whitelist import RoleEnum, WhatsappWhitelist
from app.routes.auth import admin_access_required
from app.utils.wa_gateway import WAGatewayService, format_phone_number
from app.utils.response_helper import json_success_response, json_error_response

logger = logging.getLogger("main")

router = APIRouter(tags=["WhatsApp"])


class WABotHandler:
    """Handler for WhatsApp bot commands."""
    
    # Command patterns
    COMMANDS = {
        "help": ["help", "bantuan", "command", "perintah"],
        "status": ["status", "stat", "info"],
        "cameras": ["cameras", "kamera", "list", "daftar"],
        "health": ["health", "kesehatan", "check"],
        "snapshot": ["snapshot", "foto", "capture"],
        "report": ["report", "laporan", "rekap"],
    }
    
    def __init__(self, db: Session):
        self.db = db
        self.wa_service = WAGatewayService(db)
    
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
    
    def get_command_type(self, cmd: str) -> str:
        """Get command type from alias."""
        for cmd_type, aliases in self.COMMANDS.items():
            if cmd in aliases:
                return cmd_type
        return "unknown"
    
    def handle(self, sender: str, message: str) -> str:
        """
        Handle incoming message and return response.
        
        Args:
            sender: Sender phone number
            message: Message text
        
        Returns:
            Response message
        """
        cmd, args = self.parse_command(message)
        cmd_type = self.get_command_type(cmd)
        
        logger.info(f"WA Bot: {sender} sent command '{cmd}' (type: {cmd_type})")
        
        # Command handlers
        handlers = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "cameras": self._cmd_cameras,
            "health": self._cmd_health,
            "snapshot": self._cmd_snapshot,
            "report": self._cmd_report,
        }
        
        handler = handlers.get(cmd_type, self._cmd_unknown)
        return handler(sender, args)
    
    def _cmd_help(self, sender: str, args: list) -> str:
        """Return the commands currently available from the WhatsApp workflow."""
        entry = (
            self.db.query(WhatsappWhitelist)
            .filter(WhatsappWhitelist.phone_number == sender)
            .first()
        )
        message = (
            "*Perintah B-Snap Bot*\n"
            "/help - tampilkan menu bantuan\n"
            "/ip {nama kamera} - tampilkan alamat IP kamera\n"
            "/ping {alamat IP atau hostname} - periksa koneksi host\n"
            "/cctv {nama kamera atau alamat IP} - kirim snapshot terakhir\n"
            "/snap {nama kamera atau alamat IP} - ambil snapshot terbaru\n"
        )
        if entry and entry.role == RoleEnum.admin:
            message += (
                "\n*Perintah Admin*\n"
                "/edit {nama kamera}, user: {username}, pass: {password}, "
                "ip: {alamat IP}, port: {port}, status: {status}, group: {nama grup}\n"
                "/whitelist add {nomor WA} {nama} - izinkan nomor mengakses bot\n"
                "/whitelist remove {nomor WA} - hapus izin akses bot"
            )
        return message + "\n\nContoh: /cctv Gerbang Utama"

    def _cmd_status(self, sender: str, args: list) -> str:
        """System status command."""
        try:
            # Count cameras
            total_cameras = self.db.query(Camera).filter(Camera.status.in_(["Active", "Restricted", "Maintenance"])).count()
            
            # Count unhealthy cameras
            unhealthy = (
                self.db.query(CameraHealth)
                .filter(CameraHealth.status != "healthy")
                .count()
            )
            
            # Today's snapshots (using user's configured timezone)
            tz_name = get_current_timezone(self.db)
            tz = pytz.timezone(tz_name)
            today = datetime.now(tz).date()
            today_stats = (
                self.db.query(CameraDailyStats)
                .filter(CameraDailyStats.date == today)
                .all()
            )
            total_snapshots = sum(s.snapshot_count for s in today_stats)
            
            status_emoji = "🟢" if unhealthy == 0 else "🔴"
            
            return f"""{status_emoji} *B-SNAP System Status*

📹 Cameras: {total_cameras} active
⚠️ Issues: {unhealthy} camera(s)
📸 Today's Snapshots: {total_snapshots:,}

_Last updated: {datetime.now(tz).strftime('%H:%M:%S')}_"""
            
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return "❌ Error getting system status"
    
    def _cmd_cameras(self, sender: str, args: list) -> str:
        """List cameras command."""
        try:
            cameras = (
                self.db.query(Camera)
                .filter(Camera.status.in_(["Active", "Restricted", "Maintenance"]))
                .order_by(Camera.hostname)
                .limit(20)
                .all()
            )
            
            if not cameras:
                return "📹 No cameras configured"
            
            lines = ["📹 *Camera List*\n"]
            for cam in cameras:
                status = "🟢" if cam.is_active else "🔴"
                lines.append(f"{status} {cam.name}")
            
            if len(cameras) == 20:
                lines.append("\n_...and more_")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Error listing cameras: {e}")
            return "❌ Error getting camera list"
    
    def _cmd_health(self, sender: str, args: list) -> str:
        """Health check command."""
        try:
            health_records = (
                self.db.query(CameraHealth)
                .options(joinedload(CameraHealth.camera))
                .filter(CameraHealth.status != "healthy")
                .limit(10)
                .all()
            )
            
            if not health_records:
                return "✅ All cameras are healthy!"
            
            lines = ["⚠️ *Camera Health Issues*\n"]
            for h in health_records:
                cam_name = h.camera.name if h.camera else "Unknown"
                lines.append(f"🔴 *{cam_name}*")
                lines.append(f"   Status: {h.status}")
                if h.consecutive_failures:
                    lines.append(f"   Failures: {h.consecutive_failures}")
                lines.append("")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Error getting health: {e}")
            return "❌ Error getting health status"
    
    def _cmd_snapshot(self, sender: str, args: list) -> str:
        """Get snapshot command."""
        if not args:
            return "📸 Usage: `snapshot <camera_name>`\nExample: `snapshot Camera-01`"
        
        camera_name = " ".join(args)
        
        try:
            camera = (
                self.db.query(Camera)
                .filter(Camera.hostname.ilike(f"%{camera_name}%"))
                .filter(Camera.status.in_(["Active", "Restricted", "Maintenance"]))
                .first()
            )
            
            if not camera:
                return f"❌ Camera '{camera_name}' not found"
            
            # Get latest snapshot URL (you'll need to adjust this based on your storage)
            # For now, return info about the camera
            return f"""📸 *Camera: {camera.name}*

IP: {camera.ip}
Location: {camera.location or 'N/A'}
Status: {'🟢 Active' if camera.status in ['Active', 'Restricted', 'Maintenance'] else '🔴 Inactive'}

_Snapshot feature coming soon!_"""
            
        except Exception as e:
            logger.error(f"Error getting snapshot: {e}")
            return "❌ Error getting snapshot"
    
    def _cmd_report(self, sender: str, args: list) -> str:
        """Daily report command."""
        try:
            # Get yesterday's stats (using user's configured timezone)
            tz_name = get_current_timezone(self.db)
            tz = pytz.timezone(tz_name)
            yesterday = datetime.now(tz).date() - timedelta(days=1)
            stats = (
                self.db.query(CameraDailyStats)
                .filter(CameraDailyStats.date == yesterday)
                .all()
            )
            
            if not stats:
                return f"📊 No data for {yesterday.strftime('%Y-%m-%d')}"
            
            total_snapshots = sum(s.snapshot_count for s in stats)
            top_cameras = sorted(stats, key=lambda x: x.snapshot_count, reverse=True)[:5]
            
            lines = [f"📊 *Daily Report: {yesterday.strftime('%Y-%m-%d')}*\n"]
            lines.append(f"Total Snapshots: {total_snapshots:,}\n")
            lines.append("*Top Cameras:*")
            
            for s in top_cameras:
                lines.append(f"• {s.camera_name}: {s.snapshot_count:,}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return "❌ Error generating report"
    
    def _cmd_unknown(self, sender: str, args: list) -> str:
        """Unknown command handler."""
        return """❓ Unknown command

Type `help` to see available commands."""


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

        # Handle command
        response = bot.handle(sender, message)
        
        # Send response back
        if response:
            wa_service = WAGatewayService(db)
            # GoWA documents chat_id as the target chat JID, including its
            # @s.whatsapp.net or @g.us suffix.
            recipient = str(event.get("chat_id") or sender).strip()
            send_result = wa_service.send_text(recipient, response)
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


@router.get("/api/wa/status")
def get_wa_status(
    enabled: Optional[bool] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_access_required),
):
    """Check WhatsApp gateway connection status."""
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
