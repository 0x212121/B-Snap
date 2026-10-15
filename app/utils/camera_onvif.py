import logging
from urllib.parse import quote, urlparse, urlunparse
from onvif import ONVIFCamera
from app.models.camera import Camera
from app.utils.camera_capture import open_capture

logger = logging.getLogger("snapshot")


def _mask_password_in_url(url: str) -> str:
    """Mask password in URL for logging purposes.
    
    Args:
        url: URL that may contain credentials
        
    Returns:
        URL with password masked as '***'
    """
    if not url or '@' not in url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            # Rebuild netloc with masked password
            netloc = parsed.hostname or ''
            if parsed.port:
                netloc += f":{parsed.port}"
            if parsed.username:
                auth = parsed.username
                if parsed.password:
                    auth += ":***"
                netloc = f"{auth}@{netloc}"
            parsed = parsed._replace(netloc=netloc)
            return urlunparse(parsed)
    except Exception:
        pass
    return url


def get_rtsp_url(camera: Camera, *, rtsp_port: int | None = None, try_acti: bool = False):
    """Resolve Hikvision/ONVIF streams, optionally trying ACTi live access names."""
    encoded_user = quote(camera.username or "", safe="")
    encoded_pass = quote(camera.password or "", safe="")
    base = f"rtsp://{encoded_user}:{encoded_pass}@{camera.ip}:{rtsp_port or camera.port or 554}"

    # --- 1. Coba template default Hikvision ---
    hikvision_candidates = [
        f"{base}/Streaming/Channels/101",  # mainstream
        f"{base}/Streaming/Channels/102",  # substream
    ]

    for url in hikvision_candidates:
        cap = open_capture(url)
        try:
            opened = cap.isOpened()
        finally:
            cap.release()
        if opened:
            logger.info("✅ RTSP URL resolved via Hikvision template: %s", _mask_password_in_url(url))
            return url

    # --- 2. Fallback ke ONVIF ---
    # Retry using the camera clock for WSSE timestamps; never change its clock.
    for adjust_time in ((False, True) if try_acti else (False,)):
        try:
            cam = ONVIFCamera(camera.ip, camera.port, camera.username, camera.password, adjust_time=adjust_time)
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
            logger.info("✅ RTSP URL resolved via ONVIF: %s", _mask_password_in_url(final_uri))
            return final_uri

        except Exception as e:
            logger.error("❌ Failed to get RTSP URL via ONVIF for camera %s: %s", camera.id, e)

    # ACTi access names differ by firmware. Live preview can try documented
    # stream names after ONVIF fails, preserving existing capture/record behavior.
    if try_acti:
        ports = dict.fromkeys([rtsp_port or 554, 7070])
        for port in ports:
            for path in ("stream1", "stream2", "liveview.1"):
                url = f"rtsp://{encoded_user}:{encoded_pass}@{camera.ip}:{port}/{path}"
                cap = open_capture(url)
                try:
                    opened = cap.isOpened()
                finally:
                    cap.release()
                if opened:
                    logger.info("ACTi live source resolved for camera %s on port %s", camera.id, port)
                    return url
    return None
