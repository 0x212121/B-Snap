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
            self.session.headers.update({"Authorization": f"Bearer {self.config.api_key}"})
            logger.debug("GoWA initialized with API key authentication")
        else:
            logger.debug("GoWA initialized without authentication (no API key)")
    
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
            payload = {
                "phone": phone,
                "message": message,
            }
            if reply_to:
                payload["reply_to"] = reply_to
            
            response = self.session.post(
                self._make_url("/api/send-message"),
                json=payload,
                timeout=30
            )
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
            
            response = self.session.post(
                self._make_url("/api/send-image"),
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            result = response.json()
            
            logger.info(f"WA image sent to {phone}: {result.get('status', 'unknown')}")
            return {"success": True, "data": result}
            
        except Exception as e:
            logger.error(f"Failed to send WA image to {phone}: {e}")
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
            
            response = self.session.post(
                self._make_url("/api/send-document"),
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
            
        except Exception as e:
            logger.error(f"Failed to send WA document to {phone}: {e}")
            return {"success": False, "error": str(e)}
    
    def check_connection(self) -> Dict[str, Any]:
        """Check GoWA connection status."""
        if not self.config.is_configured():
            return {"connected": False, "error": "Not configured"}
        
        try:
            response = self.session.get(
                self._make_url("/api/status"),
                timeout=10
            )
            
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
            
            return {
                "connected": result.get("connected", False),
                "logged_in": result.get("logged_in", False),
                "user": result.get("user", {}),
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
