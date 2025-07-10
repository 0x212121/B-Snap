import subprocess
from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
import logging

router = APIRouter()

logger = logging.getLogger("ping")

@router.get("/ping", response_class=PlainTextResponse)
def ping_ip(ip: str = Query(..., description="Target IP address to ping")):
    try:
        # Run ping command (Windows style)
        result = subprocess.run(
            ["ping", "-n", "4", ip],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return result.stdout
        else:
            logger.warning("Ping failed for %s: %s", ip, result.stdout or result.stderr)
            return "Ping failed for %s:\n%s" % (ip, result.stdout or result.stderr)
    except subprocess.TimeoutExpired:
        logger.error("Ping to %s timed out.", ip)
        return "Ping to %s timed out." % ip
    except Exception as e:
        logger.error("Error during ping: %s", e)
        return "Error during ping: %s" % str(e)