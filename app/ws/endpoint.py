# app/ws/endpoint.py

from fastapi import WebSocket, APIRouter
import asyncio
import asyncpg
import json
import os
import logging

router = APIRouter()
logger = logging.getLogger("websocket")

DATABASE_URL = os.getenv("DATABASE_URL")

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("🔌 WebSocket client connected")

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        queue = asyncio.Queue()

        def listener(*args):
            _, _, _, payload = args
            queue.put_nowait(payload)

        await conn.add_listener("camera_notifications", listener)

        while True:
            try:
                payload_str = await queue.get()
                payload = json.loads(payload_str)
                await websocket.send_text(json.dumps(payload))
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON payload received: {e}")
            except Exception as e:
                logger.warning(f"Error sending WebSocket message: {e}")
                break

    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
    finally:
        if conn:
            await conn.close()
        await websocket.close()
        logger.info("❌ WebSocket client disconnected")
