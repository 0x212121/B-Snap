import logging
import cv2
from urllib.parse import quote, urlparse, urlunparse
from onvif import ONVIFCamera
from app.models.camera import Camera

logger = logging.getLogger("snapshot")

def get_rtsp_url(camera: Camera):
    """Ambil RTSP URL kamera Hikvision, fallback ke ONVIF jika perlu."""
    encoded_user = quote(camera.username or "", safe="")
    encoded_pass = quote(camera.password or "", safe="")
    base = f"rtsp://{encoded_user}:{encoded_pass}@{camera.ip}:{camera.port or 554}"

    # --- 1. Coba template default Hikvision ---
    hikvision_candidates = [
        f"{base}/Streaming/Channels/101",  # mainstream
        f"{base}/Streaming/Channels/102",  # substream
    ]

    for url in hikvision_candidates:
        cap = cv2.VideoCapture(url)
        if cap.isOpened():
            cap.release()
            logger.info("✅ RTSP URL resolved via Hikvision template: %s", url)
            return url
        cap.release()

    # --- 2. Fallback ke ONVIF ---
    try:
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        media_service = cam.create_media_service()
        profile = media_service.GetProfiles()[0]

        uri = media_service.GetStreamUri({
            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
            "ProfileToken": profile.token
        }).Uri

        parsed = urlparse(uri)
        if not parsed.username and camera.username:
            netloc = f"{encoded_user}:{encoded_pass}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        final_uri = urlunparse(parsed)
        logger.info("✅ RTSP URL resolved via ONVIF: %s", final_uri)
        return final_uri

    except Exception as e:
        logger.error("❌ Failed to get RTSP URL via ONVIF for camera %s: %s", camera.id, e)
        return None
