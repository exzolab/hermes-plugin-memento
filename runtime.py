import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(
            target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()),
            daemon=True,
            name="memento-loop",
        )
        _loop_thread.start()
        return _loop


def run_sync(coro: Coroutine[Any, Any, Any], timeout: float = 120.0) -> Any:
    from agent.async_utils import safe_schedule_threadsafe

    future = safe_schedule_threadsafe(coro, _get_loop())
    if future is None:
        raise RuntimeError("Memento loop unavailable")
    return future.result(timeout=timeout)
