# app/ws/routes.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.ws.manager import websocket_connections
import asyncpg
import asyncio
import json
import os
import logging
from datetime import datetime, timezone, timedelta # Import timedelta

router = APIRouter()
# Pastikan logger dikonfigurasi dengan baik di aplikasi utama Anda
logger = logging.getLogger("uvicorn.error") # Menggunakan logger uvicorn agar pasti muncul

DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+psycopg2", "postgresql")
WAKE_UP_CHANNEL = "new_message_in_queue"

# Fungsi untuk listener di setiap worker
async def notification_listener(websockets: set):
    """
    Satu listener per worker, mendengarkan sinyal dan mengirim pesan
    ke semua koneksi yang dikelola oleh worker ini.
    """
    conn = None
    # Mulai dengan timestamp sedikit di masa lalu untuk menangkap pesan
    # yang mungkin masuk tepat sebelum worker ini aktif.
    last_sent_timestamp = datetime.now(timezone.utc) - timedelta(minutes=1)
    logger.info(f"Worker starting. Initial timestamp set to: {last_sent_timestamp}")

    while True:
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            
            signal_queue = asyncio.Queue()
            def signal_callback(*args):
                signal_queue.put_nowait(True)

            await conn.add_listener(WAKE_UP_CHANNEL, signal_callback)
            logger.info(f"✅ Worker is now listening on PostgreSQL channel <{WAKE_UP_CHANNEL}>")

            while True:
                await signal_queue.get()
                logger.info("Signal received! Waking up to check for new messages.")

                new_messages = await conn.fetch(
                    "SELECT payload, created_at FROM notification_queue WHERE created_at > $1 ORDER BY created_at ASC",
                    last_sent_timestamp
                )

                if not new_messages:
                    logger.warning("Woke up, but found no new messages. Timestamp might be off.")
                    continue

                logger.info(f"Found {len(new_messages)} new message(s) in the queue.")

                # Penting: Cek apakah ada klien yang terhubung ke worker ini
                if not websockets:
                    logger.warning("No connected clients in this worker to send notifications to.")
                    # Tetap update timestamp agar tidak mengambil pesan ini lagi
                    last_sent_timestamp = new_messages[-1]['created_at']
                    continue

                logger.info(f"Broadcasting to {len(websockets)} client(s) connected to this worker.")
                for record in new_messages:
                    payload_str = record['payload']
                    for ws in list(websockets):
                        try:
                            await ws.send_text(payload_str)
                        except Exception as e:
                            logger.error(f"Failed to send to a client: {e}")
                
                last_sent_timestamp = new_messages[-1]['created_at']
                logger.info(f"Broadcast complete. New timestamp is: {last_sent_timestamp}")

        except (asyncpg.PostgresConnectionError, OSError) as e:
            logger.error(f"Listener connection error: {e}. Retrying...")
            if conn: await conn.close()
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unhandled listener error: {e}. Retrying...")
            if conn: await conn.close()
            await asyncio.sleep(10)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    logger.info(f"Client {websocket.client} connected. Total connections in this worker: {len(websocket_connections)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"Client {websocket.client} disconnected.")
    finally:
        websocket_connections.discard(websocket)