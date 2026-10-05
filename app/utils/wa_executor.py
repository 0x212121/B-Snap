"""Bounded worker pools for blocking WhatsApp and camera operations."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _worker_count(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s; using default %s", name, default)
        return default
    if not 1 <= value <= maximum:
        logger.warning("%s must be between 1 and %s; using default %s", name, maximum, default)
        return default
    return value


# Separate pools keep a slow camera from occupying every GoWA sender thread,
# and vice versa. Limits apply per application worker process.
_CAMERA_WORKERS = _worker_count("WA_CAMERA_THREAD_WORKERS", 4, 16)
_GATEWAY_WORKERS = _worker_count("WA_GATEWAY_THREAD_WORKERS", 8, 32)
_CAMERA_EXECUTOR = ThreadPoolExecutor(
    max_workers=_CAMERA_WORKERS,
    thread_name_prefix="wa-camera",
)
_GATEWAY_EXECUTOR = ThreadPoolExecutor(
    max_workers=_GATEWAY_WORKERS,
    thread_name_prefix="wa-gateway",
)
_CAMERA_SUBMISSIONS = asyncio.Semaphore(_CAMERA_WORKERS * 2)
_GATEWAY_SUBMISSIONS = asyncio.Semaphore(_GATEWAY_WORKERS * 2)


async def run_camera_blocking(function: Callable[..., Any], *args: Any) -> Any:
    """Run a blocking camera operation in the bounded camera pool."""
    loop = asyncio.get_running_loop()
    async with _CAMERA_SUBMISSIONS:
        return await loop.run_in_executor(_CAMERA_EXECUTOR, function, *args)


async def run_gateway_blocking(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a blocking GoWA operation in the bounded gateway pool."""
    loop = asyncio.get_running_loop()
    async with _GATEWAY_SUBMISSIONS:
        return await loop.run_in_executor(_GATEWAY_EXECUTOR, lambda: function(*args, **kwargs))
