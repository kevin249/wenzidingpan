"""Own one cancellable MCP connection inside a background thread."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable


class McpConnection:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._cancel_requested = False

    async def run(
        self, operation: Callable[[], Awaitable[Any]],
        stopping: threading.Event, changed: threading.Event,
    ) -> Any:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            self._cancel_requested = False
        try:
            # stop/config changes can race the registration above.
            if stopping.is_set() or changed.is_set():
                raise asyncio.CancelledError()
            return await operation()
        finally:
            with self._lock:
                self._loop = None
                self._task = None

    def cancel(self) -> None:
        with self._lock:
            loop, task = self._loop, self._task
            if loop is None or task is None or self._cancel_requested:
                return
            self._cancel_requested = True
        try:
            # Cancel the owner, so SDK context managers unwind in their own task.
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            # asyncio.run() may have closed the loop after our snapshot.
            if not loop.is_closed():
                raise
