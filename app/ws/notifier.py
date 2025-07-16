import asyncio
import asyncpg
from fastapi import WebSocket

connected_websockets = set()

async def pg_listen_and_broadcast(dsn):
    conn = await asyncpg.connect(dsn)
    await conn.add_listener("camera_notifications", notification_handler)

    try:
        while True:
            await asyncio.sleep(1)
    finally:
        await conn.close()

def notification_handler(connection, pid, channel, payload):
    print(f"📢 Notification on {channel}: {payload}")
    for ws in connected_websockets.copy():
        try:
            asyncio.create_task(ws.send_text(payload))
        except Exception:
            connected_websockets.remove(ws)
