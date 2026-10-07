"""PostgreSQL notification listener with reconnect, heartbeat and graceful shutdown."""

from __future__ import annotations

import asyncio
import os
import signal

from contextlib import suppress
from datetime import UTC, datetime

import asyncpg

from app.core.service_health import clear_heartbeat, write_heartbeat

PG_NOTIFY_CHANNEL = "camera_notifications"
WAKE_UP_CHANNEL = "new_message_in_queue"
PING_INTERVAL = 30
RETRY_INTERVAL = 5


def log(msg: str) -> None:
    """Write a timestamped operational message without notification contents."""
    now = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    try:
        print(f"[{now}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{now}] {msg.encode('ascii', 'ignore').decode('ascii')}", flush=True)


async def wait_for_stop(stop: asyncio.Event, timeout: float) -> None:
    """Wait between checks without delaying an incoming shutdown signal."""
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=timeout)


class NotificationListener:
    """Serialize listener commands and track deliveries until shutdown."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self.connection = connection
        self.lock = asyncio.Lock()
        self.pending: set[asyncio.Task] = set()

    async def process_notification(self, payload: str) -> None:
        """Queue the payload and publish its wake-up signal in one transaction."""
        try:
            # asyncpg cannot run simultaneous commands on the same connection.
            async with self.lock, self.connection.transaction():
                await self.connection.execute(
                    "INSERT INTO notification_queue (payload) VALUES ($1)", payload
                )
                await self.connection.execute(f"NOTIFY {WAKE_UP_CHANNEL}")
        except Exception as exc:
            log(f"Failed to process notification ({type(exc).__name__})")

    def callback(
        self, _connection: asyncpg.Connection, _pid: int, _channel: str, payload: str
    ) -> None:
        """Track asynchronous notification processing for an orderly drain."""
        task = asyncio.create_task(self.process_notification(payload))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def run(self, stop: asyncio.Event) -> None:
        """Refresh health only while the registered listener connection responds."""
        self.connection.add_termination_listener(connection_terminated)
        await self.connection.add_listener(PG_NOTIFY_CHANNEL, self.callback)
        log(f"Listening on channel: {PG_NOTIFY_CHANNEL}")
        while not stop.is_set():
            async with self.lock:
                await self.connection.fetchval("SELECT 1")
            if self.connection.is_closed():
                raise ConnectionError("Listener connection closed")
            write_heartbeat("notifier")
            await wait_for_stop(stop, PING_INTERVAL)

    async def _drain(self) -> None:
        if not self.connection.is_closed():
            async with self.lock:
                await self.connection.remove_listener(PG_NOTIFY_CHANNEL, self.callback)
        if self.pending:
            await asyncio.gather(*tuple(self.pending), return_exceptions=True)

    async def close(self) -> None:
        """Stop receiving events, finish pending deliveries and close the connection."""
        try:
            await asyncio.wait_for(self._drain(), timeout=30)
        except (Exception, asyncio.CancelledError) as exc:
            for task in tuple(self.pending):
                task.cancel()
            await asyncio.gather(*tuple(self.pending), return_exceptions=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            log(f"Notifier drain failed ({type(exc).__name__})")
        finally:
            try:
                await self.connection.close(timeout=5)
            except Exception:
                self.connection.terminate()


def connection_terminated(_connection: asyncpg.Connection) -> None:
    """Invalidate health immediately if PostgreSQL terminates the listener."""
    clear_heartbeat("notifier")


async def pg_listen_forever(stop: asyncio.Event | None = None) -> None:
    """Reconnect after database failures until a shutdown signal is received."""
    stop = stop if stop is not None else asyncio.Event()
    clear_heartbeat("notifier")
    raw_dsn = os.getenv("DATABASE_URL")
    if not raw_dsn:
        raise RuntimeError("DATABASE_URL is required for notifier")
    dsn = raw_dsn.replace("postgresql+psycopg2", "postgres")
    while not stop.is_set():
        listener = None
        try:
            conn = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
            listener = NotificationListener(conn)
            await listener.run(stop)
        except Exception as exc:
            # Do not expose DSNs, payloads or credentials in connection errors.
            log(f"Notifier connection failed ({type(exc).__name__}); reconnecting.")
        finally:
            clear_heartbeat("notifier")
            if listener is not None:
                await listener.close()
        if not stop.is_set():
            await wait_for_stop(stop, RETRY_INTERVAL)
    log("Notifier stopped.")


async def main() -> None:
    """Register termination handlers and run the dedicated notifier process."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    await pg_listen_forever(stop)


if __name__ == "__main__":
    asyncio.run(main())
