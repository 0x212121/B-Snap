"""
GoWA (Aldinokemal) WhatsApp Gateway Integration
API Documentation: https://github.com/aldinokemal/go-whatsapp-web-multidevice

Setup:
1. Run GoWA: docker run -d --name gowa -p 3000:3000 aldinokemal/go-whatsapp-web-multidevice
2. Optional: Add -e AUTH_TOKEN=your_token for API authentication
3. Scan QR code at http://localhost:3000
4. Configure webhook (optional): POST /webhook/gowa
"""
import logging
import requests
import base64
import mimetypes
from pathlib import Path
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from app.models.config import Configuration

logger = logging.getLogger("main")


class GoWAConfig:
    """GoWA Configuration manager."""
    
    def __init__(self, db: Session = None):
        self.db = db
        self._cache = {}
    
    def get(self, key: str, default=None) -> str:
        """Get config value from database."""
        if key in self._cache:
            return self._cache[key]
        
        if not self.db:
            return default
            
        try:
            config = self.db.query(Configuration).filter_by(key=key).first()
            value = config.value if config else default
            self._cache[key] = value
            return value
        except Exception as e:
            logger.error(f"Error getting config {key}: {e}")
            return default
    
    @property
    def enabled(self) -> bool:
        return self.get("gowa_enabled", "0") == "1"
    
    @property
    def base_url(self) -> str:
        return self.get("gowa_base_url", "http://localhost:3000")
    
    @property
    def api_key(self) -> Optional[str]:
        """
        API key for GoWA authentication.
        Only required if GoWA is started with AUTH_TOKEN environment variable.
        Default GoWA (without AUTH_TOKEN) does not require API key.
        """
        key = self.get("gowa_api_key", "")
        return key if key else None

    @property
    def device_id(self) -> Optional[str]:
        """Optional GoWA device ID for multi-device deployments."""
        value = self.get("gowa_device_id", "")
        return value.strip() or None
    
    @property
    def default_receiver(self) -> Optional[str]:
        """Default receiver for scheduled reports."""
        return self.get("gowa_default_receiver", None)
    
    def is_configured(self) -> bool:
        """Check if GoWA is properly configured."""
        if not self.enabled:
            return False
        if not self.base_url:
            return False
        return True


class WAGatewayService:
    """Service for interacting with GoWA API."""
    
    def __init__(self, db: Session = None):
        self.config = GoWAConfig(db)
        self.session = requests.Session()
        if self.config.api_key:
            self.session.headers.update(self._auth_headers(self.config.api_key))
            logger.debug("GoWA initialized with authentication")
        else:
            logger.debug("GoWA initialized without authentication (no API key)")
        if self.config.device_id:
            self.session.headers["X-Device-Id"] = self.config.device_id

    @staticmethod
    def _auth_headers(secret: str) -> Dict[str, str]:
        """Build GoWA auth headers.

        Current GoWA uses HTTP Basic auth (`APP_BASIC_AUTH=user:pass`).
        Older deployments may use bearer-style tokens, so keep bearer as fallback metadata.
        """
        secret = (secret or "").strip()
        if not secret:
            return {}
        if ":" in secret:
            encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        return {"Authorization": f"Bearer {secret}"}
    
    def _make_url(self, endpoint: str) -> str:
        """Build full URL from endpoint."""
        base = self.config.base_url.rstrip("/")
        endpoint = endpoint.lstrip("/")
        return f"{base}/{endpoint}"
    
    def send_text(self, phone: str, message: str, reply_to: Optional[str] = None) -> Dict[str, Any]:
        """
        Send text message via GoWA.
        
        Args:
            phone: Phone number with country code (e.g., 6281234567890)
            message: Message text
            reply_to: Message ID to reply to (optional)
        
        Returns:
            Response from API
        """
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}
        
        try:
            # GoWA expects individual recipients as WhatsApp JIDs; group JIDs
            # and already-qualified personal JIDs are passed through unchanged.
            if "@" not in phone:
                phone = f"{phone}@s.whatsapp.net"
            payload = {
                "phone": phone,
                "message": message,
            }
            if reply_to:
                payload["reply_message_id"] = reply_to
            
            response = self.session.post(self._make_url("/send/message"), json=payload, timeout=30)
            if response.status_code == 404:
                response = self.session.post(self._make_url("/api/send-message"), json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"WA message sent to {phone}: {result.get('status', 'unknown')}")
            return {
                "success": True,
                "data": result,
                "message_id": result.get("id")
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send WA message to {phone}: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error sending WA: {e}")
            return {"success": False, "error": str(e)}
    
    def send_image(self, phone: str, image_url: str, caption: Optional[str] = None) -> Dict[str, Any]:
        """
        Send image message via GoWA.
        
        Args:
            phone: Phone number with country code
            image_url: URL to image
            caption: Image caption (optional)
        """
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}
        
        try:
            payload = {
                "phone": phone,
                "image": image_url,
            }
            if caption:
                payload["caption"] = caption
            
            response = self.session.post(self._make_url("/send/image"), json=payload, timeout=60)
            if response.status_code == 404:
                response = self.session.post(self._make_url("/api/send-image"), json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"WA image sent to {phone}: {result.get('status', 'unknown')}")
            return {"success": True, "data": result}
            
        except Exception as e:
            logger.error(f"Failed to send WA image to {phone}: {e}")
            return {"success": False, "error": str(e)}

    def send_image_file(
        self,
        phone: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send local image file via GoWA multipart upload."""
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}

        path = Path(image_path)
        if not path.exists():
            return {"success": False, "error": f"Image not found: {image_path}"}

        try:
            if "@" not in phone:
                phone = f"{phone}@s.whatsapp.net"
            data = {"phone": phone}
            if caption:
                data["caption"] = caption
            if reply_to:
                data["reply_message_id"] = reply_to
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            with path.open("rb") as file_obj:
                files = {"image": (path.name, file_obj, content_type)}
                response = self.session.post(self._make_url("/send/image"), data=data, files=files, timeout=60)
            response.raise_for_status()
            result = response.json()
            logger.info("WA image sent to %s: %s", phone, result.get("message", result.get("status", "success")))
            return {"success": True, "data": result, "message_id": result.get("results", {}).get("message_id")}
        except Exception as e:
            logger.error(f"Failed to send WA image file to {phone}: {e}")
            return {"success": False, "error": str(e)}
    
    def send_document(self, phone: str, document_url: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Send document via GoWA."""
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}
        
        try:
            payload = {
                "phone": phone,
                "document": document_url,
            }
            if filename:
                payload["filename"] = filename
            
            response = self.session.post(self._make_url("/send/file"), json=payload, timeout=60)
            if response.status_code == 404:
                response = self.session.post(self._make_url("/api/send-document"), json=payload, timeout=60)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
            
        except Exception as e:
            logger.error(f"Failed to send WA document to {phone}: {e}")
            return {"success": False, "error": str(e)}

    def send_file(self, phone: str, file_path: str, caption: Optional[str] = None) -> Dict[str, Any]:
        """Send local file via GoWA multipart upload."""
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}

        path = Path(file_path)
        if not path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        try:
            data = {"phone": phone}
            if caption:
                data["caption"] = caption
            with path.open("rb") as file_obj:
                files = {"file": (path.name, file_obj, "application/pdf")}
                response = self.session.post(self._make_url("/send/file"), data=data, files=files, timeout=60)
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except Exception as e:
            logger.error(f"Failed to send WA file to {phone}: {e}")
            return {"success": False, "error": str(e)}
    
    def check_connection(self) -> Dict[str, Any]:
        """Check GoWA connection status."""
        if not self.config.is_configured():
            return {"connected": False, "error": "Not configured"}
        
        try:
            response = self.session.get(self._make_url("/app/status"), timeout=10)
            if response.status_code == 404:
                response = self.session.get(self._make_url("/api/status"), timeout=10)
            
            # Check content type to detect HTML error pages
            content_type = response.headers.get('content-type', '')
            if 'text/html' in content_type:
                logger.error(f"GoWA returned HTML instead of JSON. Status: {response.status_code}")
                return {
                    "connected": False, 
                    "error": f"GoWA returned HTML (not running or wrong URL). Check if GoWA is running at {self.config.base_url}"
                }
            
            response.raise_for_status()
            
            try:
                result = response.json()
            except ValueError as e:
                logger.error(f"GoWA returned invalid JSON: {e}")
                return {
                    "connected": False,
                    "error": "Invalid response from GoWA (not valid JSON)"
                }
            
            status_payload = result.get("results", result)
            return {
                "connected": status_payload.get("connected", status_payload.get("is_connected", False)),
                "logged_in": status_payload.get(
                    "logged_in",
                    status_payload.get("is_logged_in", status_payload.get("connected", False)),
                ),
                "user": status_payload.get("user", status_payload),
                "error": None
            }
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"GoWA connection refused: {e}")
            return {
                "connected": False, 
                "error": f"Cannot connect to GoWA at {self.config.base_url}. Is GoWA running?"
            }
        except requests.exceptions.Timeout as e:
            logger.error(f"GoWA connection timeout: {e}")
            return {"connected": False, "error": "Connection to GoWA timed out"}
        except Exception as e:
            logger.error(f"GoWA connection check failed: {e}")
            return {"connected": False, "error": str(e)}

    def list_groups(self) -> Dict[str, Any]:
        """List WhatsApp groups available to the connected GoWA account."""
        if not self.config.is_configured():
            return {"success": False, "error": "GoWA not configured"}

        try:
            response = self.session.get(self._make_url("/user/my/groups"), timeout=15)
            response.raise_for_status()
            result = response.json()
            payload = result.get("results", result)
            raw_groups = payload.get("data", payload if isinstance(payload, list) else [])

            groups = []
            for item in raw_groups:
                if not isinstance(item, dict):
                    continue
                jid = item.get("JID") or item.get("jid") or item.get("id")
                name = item.get("Name") or item.get("name") or item.get("subject") or jid
                if not jid:
                    continue
                groups.append(
                    {
                        "jid": jid,
                        "name": name,
                        "participants": item.get("ParticipantCount") or item.get("participants_count"),
                    }
                )

            return {"success": True, "groups": groups}
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to list GoWA groups: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Unexpected error listing GoWA groups: {e}")
            return {"success": False, "error": str(e)}


def send_wa_notification(db: Session, phone: str, message: str, notification_type: str = "alert") -> bool:
    """
    Send WA notification - convenience function.
    
    Args:
        db: Database session
        phone: Target phone number
        message: Message content
        notification_type: Type for logging (alert, status, bot, etc)
    
    Returns:
        True if sent successfully
    """
    service = WAGatewayService(db)
    result = service.send_text(phone, message)
    
    if result["success"]:
        logger.info(f"WA {notification_type} sent to {phone}")
    else:
        logger.error(f"Failed to send WA {notification_type} to {phone}: {result.get('error')}")
    
    return result["success"]


def format_phone_number(phone: str) -> str:
    """
    Format phone number to GoWA format.
    - Remove spaces, dashes, plus
    - Ensure starts with country code (62 for Indonesia)
    """
    # Remove all non-digit characters
    digits = ''.join(filter(str.isdigit, phone))
    
    # Convert leading 0 to 62
    if digits.startswith('0'):
        digits = '62' + digits[1:]
    
    return digits
