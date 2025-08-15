import logging
import time
import requests
import cv2
from ping3 import ping
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

logger = logging.getLogger("snapshot")

def is_reachable(ip: str, timeout_ms: int = 1000) -> bool:
    """Ping IP sekali, return True jika reachable."""
    try:
        response = ping(ip, timeout=timeout_ms / 1000)
        return response is not None
    except Exception as e:
        logger.debug(f"Ping error to {ip}: {e}")
        return False


def try_auth(uri: str, username: str, password: str):
    """Coba HTTP Digest lalu fallback ke Basic Auth."""
    try:
        r = requests.get(uri, auth=HTTPDigestAuth(username, password), timeout=5)
        if r.status_code == 401:
            logger.warning(f"Digest failed ({r.status_code}), trying Basic Auth...")
            r = requests.get(uri, auth=HTTPBasicAuth(username, password), timeout=5)
        if not r.ok:
            raise RuntimeError(f"Auth HTTP {r.status_code}")
        return r
    except Exception as e:
        raise RuntimeError(f"Auth failed: {e}") from e


def stream_exists_opencv(rtsp_url: str, timeout: float = 5.0) -> bool:
    """Cek apakah RTSP stream bisa dibuka oleh OpenCV."""
    start = time.time()
    cap = cv2.VideoCapture()
    try:
        cap.open(rtsp_url)
        while not cap.isOpened():
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)
        return True
    finally:
        cap.release()
