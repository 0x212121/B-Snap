from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
import logging
from ping3 import ping, exceptions

router = APIRouter()

logger = logging.getLogger("ping")

@router.get("/ping", response_class=PlainTextResponse)
def ping_ip(ip: str = Query(..., description="Target IP address to ping")):
    try:
        # Perform 4 ping attempts manually using ping3
        responses = []
        for i in range(4):
            delay = ping(ip, timeout=2)  # timeout in seconds
            if delay is None:
                responses.append(f"Request timeout for attempt {i+1}")
            else:
                ms = round(delay * 1000, 2)
                responses.append(f"Reply from {ip}: time={ms}ms")
        
        return "\n".join(responses)
    
    except exceptions.PingError as e:
        logger.error("Ping error to %s: %s", ip, str(e))
        return f"Ping error to {ip}: {str(e)}"
    except Exception as e:
        logger.error("Unexpected error during ping to %s: %s", ip, e)
        return f"Unexpected error during ping to {ip}: {str(e)}"
