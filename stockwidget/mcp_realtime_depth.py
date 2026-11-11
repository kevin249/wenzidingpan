"""盘口使用独立线程/事件循环，JSON schema 校验不阻塞普通事件。"""
import asyncio
import queue
import threading

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAX_PENDING_SYMBOLS = 50
RETRY_SECONDS = 3
IDLE_SECONDS = 0.05


class DepthUpdates:
    """同一股票只排队一次；始终读取服务端最新盘口版本。"""
    def __init__(self):
        self.pending = {}
        self.lock = threading.Lock()

    def full(self):
        with self.lock:
            return len(self.pending) >= MAX_PENDING_SYMBOLS

    def put_nowait(self, symbol):
        with self.lock:
            if symbol not in self.pending and len(self.pending) >= MAX_PENDING_SYMBOLS:
                raise queue.Full
            self.pending[symbol] = None

    def get_nowait(self):
        with self.lock:
            if not self.pending:
                raise queue.Empty
            symbol = next(iter(self.pending))
            del self.pending[symbol]
            return symbol


class DepthWorker:
    def __init__(self, listener, config):
        self.listener, self.config = listener, config
        self.updates = DepthUpdates()
        self.stopping = threading.Event()
        self.loop, self.task = None, None
        self.thread = threading.Thread(target=self._run, name='mcp-realtime-depth-client', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.stopping.set()
        loop, task = self.loop, self.task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def _run(self):
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        finally:
            self.loop, self.task = None, None

    async def _serve(self):
        from .mcp_notifications import SSE_TIMEOUT, _api_key
        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        while not self.stopping.is_set():
            try:
                async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + _api_key(self.config)}, timeout=SSE_TIMEOUT) as client:
                    async with streamable_http_client(self.config.mcp_realtime_url, http_client=client,
                                                       terminate_on_close=False) as (rd, wr):
                        async with ClientSession(rd, wr) as session:
                            await asyncio.wait_for(session.discover(), 15)
                            await self._consume(session)
            except Exception:
                self.listener._emit_status('盘口连接暂不可用，事件连接继续；稍后重试', level='WARN')
                await asyncio.sleep(RETRY_SECONDS)

    async def _consume(self, session):
        from .mcp_realtime import emit_depth
        retries = {}
        while not self.stopping.is_set():
            try:
                symbol = self.updates.get_nowait()
            except queue.Empty:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            if retries.get(symbol, 0) > self.loop.time():
                self.updates.put_nowait(symbol)
                await asyncio.sleep(IDLE_SECONDS)
                continue
            try:
                received = await emit_depth(self.listener, session, symbol)
            except Exception:
                self.updates.put_nowait(symbol)
                raise
            if received:
                retries.pop(symbol, None)
            else:
                retries[symbol] = self.loop.time() + RETRY_SECONDS
                self.updates.put_nowait(symbol)
