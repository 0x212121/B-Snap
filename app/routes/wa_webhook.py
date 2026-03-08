"""
GoWA Webhook Handler - WhatsApp Bot Integration
Handles incoming messages from GoWA webhook and responds to commands.
"""
import logging
import re
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import Optional

from app.db.database import get_db
from app.models.user import User
from app.models.camera import Camera
from app.models.health import CameraHealth
from app.models.camera_daily_stats import CameraDailyStats
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
        """Help command."""
        return """🤖 *B-SNAP WhatsApp Bot Commands*

📋 *Status Commands:*
• `status` - System overview
• `cameras` - List all cameras
• `health` - Camera health check
• `report` - Daily snapshot report

📸 *Camera Commands:*
• `snapshot <camera_name>` - Get latest snapshot

📝 *Examples:*
• `status`
• `cameras`
• `snapshot Camera-01`

Type any command to get started!"""
    
    def _cmd_status(self, sender: str, args: list) -> str:
        """System status command."""
        try:
            # Count cameras
            total_cameras = self.db.query(Camera).filter(Camera.is_active == True).count()
            
            # Count unhealthy cameras
            unhealthy = (
                self.db.query(CameraHealth)
                .filter(CameraHealth.status != "healthy")
                .count()
            )
            
            # Today's snapshots
            today = datetime.now().date()
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

_Last updated: {datetime.now().strftime('%H:%M:%S')}_"""
            
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return "❌ Error getting system status"
    
    def _cmd_cameras(self, sender: str, args: list) -> str:
        """List cameras command."""
        try:
            cameras = (
                self.db.query(Camera)
                .filter(Camera.is_active == True)
                .order_by(Camera.name)
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
                .filter(Camera.name.ilike(f"%{camera_name}%"))
                .filter(Camera.is_active == True)
                .first()
            )
            
            if not camera:
                return f"❌ Camera '{camera_name}' not found"
            
            # Get latest snapshot URL (you'll need to adjust this based on your storage)
            # For now, return info about the camera
            return f"""📸 *Camera: {camera.name}*

IP: {camera.ip}
Location: {camera.location or 'N/A'}
Status: {'🟢 Active' if camera.is_active else '🔴 Inactive'}

_Snapshot feature coming soon!_"""
            
        except Exception as e:
            logger.error(f"Error getting snapshot: {e}")
            return "❌ Error getting snapshot"
    
    def _cmd_report(self, sender: str, args: list) -> str:
        """Daily report command."""
        try:
            # Get yesterday's stats
            yesterday = datetime.now().date() - timedelta(days=1)
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
    
    GoWA Payload Example:
    {
        "from": "6281234567890",
        "message": "status",
        "timestamp": 1234567890,
        "message_id": "ABC123"
    }
    """
    try:
        payload = await request.json()
        logger.debug(f"GoWA Webhook received: {payload}")
        
        # Extract data
        sender = payload.get("from", "")
        message = payload.get("message", "")
        
        if not sender or not message:
            return json_success_response("Webhook received (empty)")
        
        # Format sender number
        sender = format_phone_number(sender)
        
        # Handle command
        bot = WABotHandler(db)
        response = bot.handle(sender, message)
        
        # Send response back
        if response:
            wa_service = WAGatewayService(db)
            wa_service.send_text(sender, response)
        
        return json_success_response("Message processed")
        
    except Exception as e:
        logger.error(f"Error processing GoWA webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/wa/status")
def get_wa_status(db: Session = Depends(get_db)):
    """Check WhatsApp gateway connection status."""
    service = WAGatewayService(db)
    status = service.check_connection()
    
    if status["connected"]:
        return json_success_response("WhatsApp connected", status)
    else:
        return json_error_response(status.get("error", "Not connected"), 503)


@router.post("/api/wa/send-test")
def send_test_wa(
    phone: str,
    message: str = "🤖 Test message from B-SNAP",
    db: Session = Depends(get_db)
):
    """Send test WhatsApp message."""
    service = WAGatewayService(db)
    phone = format_phone_number(phone)
    
    result = service.send_text(phone, message)
    
    if result["success"]:
        return json_success_response("Message sent", result)
    else:
        return json_error_response(result.get("error", "Failed to send"), 500)
