from typing import Set
from fastapi import WebSocket

# Set global WebSocket connections
websocket_connections: Set[WebSocket] = set()

def get_ws_connections() -> Set[WebSocket]:
    return websocket_connections
