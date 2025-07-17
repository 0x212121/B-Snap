# notifier.py
import asyncio
import json
import asyncpg
import os
from datetime import datetime, timezone

PG_NOTIFY_CHANNEL = "camera_notifications"
WAKE_UP_CHANNEL = "new_message_in_queue" # Channel sinyal untuk worker

def log(msg: str):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"[{now}] {msg}", flush=True)

async def pg_listen_forever():
    raw_dsn = os.getenv("DATABASE_URL")
    if not raw_dsn:
        log("❌ DATABASE_URL not found in environment")
        return

    dsn = raw_dsn.replace("postgresql+psycopg2", "postgres")
    # await wait_for_postgres(dsn) # Pastikan DB siap

    while True:
        try:
            conn = await asyncpg.connect(dsn)
            log(f"🔌 Connected to PostgreSQL, listening on channel: {PG_NOTIFY_CHANNEL}")

            # Callback ini dipanggil saat ada notifikasi dari kamera
            async def db_event_callback(connection, pid, channel, payload):
                log(f"📢 NOTIFY received on <{channel}>: {payload}")
                try:
                    # 1. Masukkan payload ke tabel antrean
                    await connection.execute(
                        "INSERT INTO notification_queue (payload) VALUES ($1)",
                        payload
                    )
                    log(f"✅ Inserted payload into notification_queue")

                    # 2. Kirim sinyal ke semua worker
                    await connection.execute(f"NOTIFY {WAKE_UP_CHANNEL}")
                    log(f"✅ Sent wake-up signal on <{WAKE_UP_CHANNEL}>")

                except Exception as e:
                    log(f"⚠️ Failed to process notification: {e}")

            await conn.add_listener(PG_NOTIFY_CHANNEL, db_event_callback)

            while True:
                await asyncio.sleep(3600)

        except (asyncpg.PostgresConnectionError, OSError) as e:
            log(f"⚠️ Lost connection to PostgreSQL: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            log(f"❌ Unhandled exception: {e}. Retrying in 10s...")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(pg_listen_forever())