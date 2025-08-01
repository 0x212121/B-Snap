from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.ws.manager import websocket_connections
import asyncpg
import asyncio
import json
import os
import logging

router = APIRouter()
# Pastikan logger dikonfigurasi dengan baik di aplikasi utama Anda
logger = logging.getLogger("uvicorn.error") # Menggunakan logger uvicorn agar pasti muncul

DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+psycopg2", "postgresql")
WAKE_UP_CHANNEL = "new_message_in_queue"
POLLING_INTERVAL = 5  # Detik. Atur sesuai kebutuhan Anda.


async def notification_listener(websockets: set):
    """
    Listener yang menggunakan metode polling untuk keandalan maksimal.
    Mengecek database secara berkala untuk pesan baru.
    """
    last_processed_id = 0
    logger.info(f"🚀 Polling listener started. Checking for new messages every {POLLING_INTERVAL} seconds.")

    # Inisialisasi dengan ID terakhir dari database saat startup
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        last_record = await conn.fetchrow("SELECT id FROM notification_queue ORDER BY id DESC LIMIT 1")
        if last_record:
            last_processed_id = last_record['id']
            logger.info(f"Initial last_processed_id set to: {last_processed_id}")
        await conn.close()
    except Exception as e:
        logger.error(f"Failed to initialize last_processed_id: {e}")

    while True:
        try:
            # Tunggu sesuai interval polling
            await asyncio.sleep(POLLING_INTERVAL)

            # Jika tidak ada klien yang terhubung di worker ini, lewati pengecekan
            if not websockets:
                continue

            conn = await asyncpg.connect(DATABASE_URL)

            new_messages = await conn.fetch(
                "SELECT id, payload FROM notification_queue WHERE id > $1 ORDER BY id ASC",
                last_processed_id
            )

            if new_messages:
                logger.info(f"Found {len(new_messages)} new message(s). Broadcasting...")
                for record in new_messages:
                    payload_data = json.loads(record['payload'])
                    payload_data['notification_id'] = record['id']
                    final_payload_str = json.dumps(payload_data)

                    for ws in list(websockets):
                        try:
                            await ws.send_text(final_payload_str)
                        except Exception:
                            pass
                
                last_processed_id = new_messages[-1]['id']
                logger.info(f"Broadcast complete. New last_processed_id is: {last_processed_id}")

        except Exception as e:
            logger.error(f"An error occurred in the polling loop: {e}")
        finally:
            if 'conn' in locals() and conn and not conn.is_closed():
                await conn.close()


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