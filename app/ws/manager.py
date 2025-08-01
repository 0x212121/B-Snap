from typing import Set
from fastapi import WebSocket, WebSocketDisconnect
import logging

logger = logging.getLogger("websocket")

# Global set to store all active connections
websocket_connections: Set[WebSocket] = set()

def get_ws_connections() -> Set[WebSocket]:
    """Get the current set of active WebSocket connections."""
    return websocket_connections

async def connect_ws(websocket: WebSocket):
    """Accept and register a WebSocket connection."""
    await websocket.accept()
    websocket_connections.add(websocket)
    logger.info(f"🔌 WebSocket connected: {websocket.client}")

def disconnect_ws(websocket: WebSocket):
    """Remove a WebSocket connection."""
    if websocket in websocket_connections:
        websocket_connections.remove(websocket)
        logger.info(f"❌ WebSocket disconnected: {websocket.client}")

async def handle_ws(websocket: WebSocket):
    """
    Central handler for incoming WebSocket connections.
    Keeps the connection open and handles disconnects.
    """
    await connect_ws(websocket)
    try:
        while True:
            # Keep connection alive. You can add logic here to handle
            # incoming messages from the client if needed.
            await websocket.receive_text()
    except WebSocketDisconnect:
        disconnect_ws(websocket)
    except Exception as e:
        logger.warning(f"⚠️ Unexpected WebSocket error: {e}")
        disconnect_ws(websocket)