"""Bridge for running coroutines on the Telethon loop from Flask threads."""

import asyncio
import threading

from bot import state

def run_async_coroutine(coro):
    if state.telethon_loop and state.telethon_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, state.telethon_loop)
    else:
        # Fallback for testing or when loop is not running yet
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

MAX_PENDING_BACKGROUND_JOBS = 64
MAX_CONCURRENT_BACKGROUND_JOBS = 8
background_jobs_pending = 0
background_jobs_lock = threading.Lock()


async def _run_bounded_background_job(coro):
    global background_jobs_pending
    started = False
    try:
        async with state.background_job_semaphore:
            started = True
            return await coro
    finally:
        if not started:
            coro.close()
        with background_jobs_lock:
            background_jobs_pending -= 1

def run_bounded_async_coroutine(coro):
    """Schedule external-API work without allowing an unbounded task backlog."""
    global background_jobs_pending
    with background_jobs_lock:
        if background_jobs_pending >= MAX_PENDING_BACKGROUND_JOBS or state.background_job_semaphore is None:
            coro.close()
            print("[BACKGROUND JOBS] Dropping job because the bounded queue is full or not ready.", flush=True)
            return False
        background_jobs_pending += 1

    try:
        run_async_coroutine(_run_bounded_background_job(coro))
        return True
    except Exception:
        with background_jobs_lock:
            background_jobs_pending -= 1
        coro.close()
        raise
