import logging
from urllib.parse import urlparse, urlunparse, quote
from onvif import ONVIFCamera
from app.models.camera import Camera

logger = logging.getLogger("snapshot")

def get_rtsp_url(camera: Camera):
    """Ambil RTSP URL dari kamera ONVIF."""
    try:
        cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password)
        media_service = cam.create_media_service()
        profile = media_service.GetProfiles()[0]

        encoded_user = quote(camera.username, safe='')
        encoded_pass = quote(camera.password, safe='')
        uri = media_service.GetStreamUri({
            'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}},
            'ProfileToken': profile.token
        }).Uri

        parsed = urlparse(uri)
        if not parsed.username and camera.username:
            netloc = f"{encoded_user}:{encoded_pass}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            parsed = parsed._replace(netloc=netloc)

        return urlunparse(parsed)
    except Exception as e:
        logger.error("Error getting RTSP URL for camera %s: %s", camera.id, e)
        return None
