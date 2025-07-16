# app/ws/routes.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.ws.manager import websocket_connections # Setiap worker punya set ini
import asyncpg
import asyncio
import json
import os
import logging
from datetime import datetime, timezone

router = APIRouter()
logger = logging.getLogger("websocket")

DATABASE_URL = os.getenv("DATABASE_URL").replace("postgresql+psycopg2", "postgresql")
WAKE_UP_CHANNEL = "new_message_in_queue"

# Fungsi untuk listener di setiap worker
async def notification_listener(websockets: set):
    """
    Satu listener per worker, mendengarkan sinyal dan mengirim pesan
    ke semua koneksi yang dikelola oleh worker ini.
    """
    conn = None
    # Menyimpan timestamp pesan terakhir yang dikirim oleh worker ini
    last_sent_timestamp = datetime.now(timezone.utc)

    while True:
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            
            # Queue untuk menerima sinyal dari PostgreSQL
            signal_queue = asyncio.Queue()
            def signal_callback(*args):
                signal_queue.put_nowait(True)

            await conn.add_listener(WAKE_UP_CHANNEL, signal_callback)
            logger.info(f"Worker listening on channel <{WAKE_UP_CHANNEL}>")

            while True:
                await signal_queue.get() # Menunggu sinyal "ada pesan baru"
                logger.info(f"Worker received wake-up signal on <{WAKE_UP_CHANNEL}>")

                # Ambil semua pesan baru dari tabel
                new_messages = await conn.fetch(
                    "SELECT payload, created_at FROM notification_queue WHERE created_at > $1 ORDER BY created_at ASC",
                    last_sent_timestamp
                )

                if not new_messages:
                    continue

                # Kirim pesan ke semua klien yang terhubung ke worker ini
                for record in new_messages:
                    payload_str = record['payload']
                    for ws in list(websockets): # Salin agar aman jika ada perubahan
                        try:
                            await ws.send_text(payload_str)
                        except Exception:
                            # Abaikan error jika koneksi sudah ditutup
                            pass
                
                # Perbarui timestamp terakhir setelah berhasil mengirim
                last_sent_timestamp = new_messages[-1]['created_at']
                logger.info(f"Worker sent {len(new_messages)} messages. Last timestamp: {last_sent_timestamp}")


        except (asyncpg.PostgresConnectionError, OSError) as e:
            logger.error(f"Listener connection error: {e}. Retrying in 5s...")
            if conn: await conn.close()
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unhandled listener error: {e}. Retrying in 10s...")
            if conn: await conn.close()
            await asyncio.sleep(10)


# Jalankan satu listener di background saat aplikasi (worker) pertama kali start
@router.on_event("startup")
async def startup_event():
    asyncio.create_task(notification_listener(websocket_connections))

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