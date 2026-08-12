"""
Helpers for running sync I/O off the asyncio event loop.

Phase 1 refactor: FastAPI handlers stay ``async def`` for UploadFile/streaming,
but Firestore / GCS / Identity Toolkit / service methods are sync. Call those
via ``run_sync`` so they execute in a worker thread and do not block the loop.

Behaviour of the underlying sync functions is unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


async def run_sync(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Await ``func(*args, **kwargs)`` in a thread-pool worker.

    Use for sync Firebase, GCS, HTTP, and service-layer work invoked from
    ``async`` routes or dependencies.
    """
    return await asyncio.to_thread(func, *args, **kwargs)
