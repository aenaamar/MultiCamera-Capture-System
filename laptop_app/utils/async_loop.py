"""Async event loop runner and thread-safe task submission utilities."""
import asyncio
import concurrent.futures
from typing import Coroutine, Any, Optional

_server_loop: Optional[asyncio.AbstractEventLoop] = None

def set_server_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the active asyncio event loop (e.g. running uvicorn)."""
    global _server_loop
    _server_loop = loop

def get_server_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Retrieve the registered asyncio event loop."""
    return _server_loop

def run_async_coro(coro: Coroutine) -> concurrent.futures.Future:
    """
    Submit a coroutine to the server's asyncio event loop from any thread (e.g. Qt UI thread).
    Returns a concurrent.futures.Future.
    """
    loop = get_server_loop()
    if loop is None or not loop.is_running():
        raise RuntimeError("Async event loop is not running or not initialized.")
    return asyncio.run_coroutine_threadsafe(coro, loop)
