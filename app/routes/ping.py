import subprocess
from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

router = APIRouter()

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
            return f"Ping failed for {ip}:\n{result.stdout or result.stderr}"
    except subprocess.TimeoutExpired:
        return f"Ping to {ip} timed out."
    except Exception as e:
        return f"Error during ping: {str(e)}"