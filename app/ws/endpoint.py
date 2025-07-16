from fastapi import APIRouter, WebSocket
from notifier import connected_websockets

router = APIRouter()

@router.websocket("/ws/notifications")
async def notifications_ws(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep connection alive
    except Exception:
        pass
    finally:
        connected_websockets.remove(websocket)
