from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
import logging
from ping3 import ping

router = APIRouter()
logger = logging.getLogger("ping")

@router.get("/ping", response_class=PlainTextResponse)
def ping_ip(ip: str = Query(..., description="Target IP address to ping")):
    try:
        responses = []
        for i in range(4):
            delay = ping(ip, timeout=2)
            if delay is None:
                responses.append(f"Request timeout for attempt {i+1}")
            else:
                ms = round(delay * 1000, 2)
                responses.append(f"Reply from {ip}: time={ms}ms")

        return "\n".join(responses)

    except Exception as e:
        logger.error("Ping to %s failed: %s", ip, e)
        return f"Ping failed for {ip}: {e}"
