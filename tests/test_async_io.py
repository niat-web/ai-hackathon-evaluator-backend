"""Phase 0/1: run_sync offloads work without changing return values."""

import asyncio
import threading

import pytest

from app.utils.async_io import run_sync


def _add(a: int, b: int) -> int:
    return a + b


def _thread_name() -> str:
    return threading.current_thread().name


@pytest.mark.asyncio
async def test_run_sync_preserves_return_value():
    assert await run_sync(_add, 2, 3) == 5
    assert await run_sync(_add, a=10, b=5) == 15


@pytest.mark.asyncio
async def test_run_sync_runs_off_event_loop_thread():
    main_thread = threading.current_thread().name
    worker_thread = await run_sync(_thread_name)
    assert worker_thread != main_thread


@pytest.mark.asyncio
async def test_run_sync_propagates_exceptions():
    def boom() -> None:
        raise ValueError("expected")

    with pytest.raises(ValueError, match="expected"):
        await run_sync(boom)


@pytest.mark.asyncio
async def test_run_sync_does_not_block_event_loop():
    """Other coroutines can proceed while a sync call is in the thread pool."""
    started = threading.Event()
    release = threading.Event()

    def blocked() -> str:
        started.set()
        release.wait(timeout=2)
        return "done"

    async def marker() -> str:
        # Runs on the event loop; succeeds only if the loop is not blocked.
        for _ in range(50):
            if started.is_set():
                return "marker"
            await asyncio.sleep(0.01)
        return "timeout"

    task = asyncio.create_task(run_sync(blocked))
    mark = await marker()
    release.set()
    assert await task == "done"
    assert mark == "marker"