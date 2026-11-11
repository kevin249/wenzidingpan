"""真实服务器事件与千档联调；独立后台任务，结果不包含凭据。"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict
import threading
import time

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import listen

from .mcp_depth import parse_depth_payload, depth_is_stale
from .mcp_endpoint import realtime_url
from .mcp_notifications import _api_key, _describe_error
from .mcp_realtime import _call, read_depth
from .symbols import classify


@asynccontextmanager
async def connection(url, key):
    async with httpx2.AsyncClient(trust_env=False, timeout=httpx2.Timeout(15, read=45),
                                 headers={'Authorization': 'Bearer ' + key}) as client:
        async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (rd, wr):
            async with ClientSession(rd, wr) as session:
                await asyncio.wait_for(session.discover(), 15)
                yield session


class McpSelfTest:
    def __init__(self, on_depth=None):
        self.on_depth = on_depth
        self.lock = threading.Lock()
        self.value = dict(running=False, steps=[], error='')

    def snapshot(self):
        with self.lock:
            return dict(self.value, steps=[dict(row) for row in self.value['steps']])

    def start(self, config):
        with self.lock:
            if self.value['running']:
                return False
            self.value = dict(running=True, steps=[], error='', symbol='')
        threading.Thread(target=self._run, args=(config,), daemon=True, name='mcp-selftest').start()
        return True

    def _update(self, **values):
        with self.lock:
            self.value.update(values)

    async def step(self, label, operation, describe=lambda value: '通过'):
        row = dict(label=label, status='running', detail='', milliseconds=None)
        with self.lock:
            self.value['steps'].append(row)
        started = time.perf_counter()
        fields = dict(status='fail', detail='测试取消或超时')
        try:
            value = await asyncio.wait_for(operation(), 25)
            fields = dict(status='pass', detail=describe(value))
        except Exception as exc:
            fields = dict(status='fail', detail=_describe_error(exc))
            raise
        finally:
            with self.lock:
                row.update(fields, milliseconds=round((time.perf_counter()-started)*1000))
        return value

    def _run(self, config):
        try:
            asyncio.run(asyncio.wait_for(self.exercise(config), 100))
        except Exception as exc:
            self._update(error=_describe_error(exc))
        finally:
            self._update(running=False)

    def deliver(self, snapshot):
        self._update(depth=asdict(snapshot) | {'levels': None}, delivered_at=time.time())
        if self.on_depth:
            self.on_depth(snapshot)

    async def exercise(self, config):
        symbols = [item.code for text in config.symbols if (item := classify(text))]
        if not symbols:
            raise ValueError('请先添加一只自选股')
        if not _api_key(config):
            raise ValueError('请先填写 MCP API Key')
        if not realtime_url(config):
            raise ValueError('请先配置独立实时订阅地址')
        code = symbols[0]
        self._update(symbol=code)
        async with AsyncExitStack() as stack:
            session = None
            try:
                session = await self.step('实时连接', lambda: stack.enter_async_context(
                    connection(realtime_url(config), _api_key(config))))
                await self.step('服务器事件回传', lambda: self.event_test(session, code))
            except Exception as exc:
                self._update(error=_describe_error(exc))
            try:
                await self.book_test(session, config, code)
            except Exception as exc:
                self._update(error=_describe_error(exc))

    async def book_test(self, session, config, code):
        captured = await self.step('真实千档采集', lambda: self.capture(config, code),
            lambda value: f"{value.get('total_bid_levels', 0)} 买档 / {value.get('total_ask_levels', 0)} 卖档")
        snapshot = parse_depth_payload(code, captured)
        self.deliver(snapshot)
        if session is not None:
            depth = await self.step('千档推送接收与校验', lambda: self.depth(session, code, snapshot),
                lambda value: f'{value.bid_count} 买档 / {value.ask_count} 卖档，校验通过')
            self.deliver(depth)

    async def event_test(self, session, code):
        stream = await _call(session, 'get_event_stream')
        recovery = await _call(session, 'begin_event_recovery')
        cursor = recovery['cursor']
        while True:
            page = await _call(session, 'get_event_delta', dict(cursor=cursor, recovery_token=recovery['recovery_token']))
            cursor = page['next_cursor']
            if not page['has_more']:
                break
        async with listen(session, resource_subscriptions=[stream['resource_uri'], stream['control_uri']]) as sub:
            queue = asyncio.Queue()
            async def watch():
                async for event in sub:
                    await queue.put(str(event.uri))
            watcher = asyncio.create_task(watch())
            try:
                return await self.event(session, queue, cursor, code)
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)

    async def event(self, session, queue, cursor, code):
        sent = await _call(session, 'request_push_test', dict(symbol=code))
        while True:
            await queue.get()
            page = await _call(session, 'get_event_delta', dict(cursor=cursor))
            cursor = page['next_cursor']
            if any(row['event_id'] == sent['event_id'] for row in page['events']):
                return sent

    async def capture(self, config, code):
        async with connection(config.mcp_url, _api_key(config)) as session:
            value = await _call(session, 'get_tdx_depth', dict(symbol=code, requested_depth=1000, force=True))
        if not value.get('available') or not value.get('distribution_ready'):
            raise RuntimeError(value.get('reason') or value.get('message') or '当前千档源不可用，未用十档代替')
        return value

    async def depth(self, session, code, expected):
        from .mcp_depth import depth_sample_time
        while True:
            value = await read_depth(session, code)
            snapshot = parse_depth_payload(code, value)
            if (snapshot.full_depth and not depth_is_stale(snapshot)
                    and depth_sample_time(snapshot) >= depth_sample_time(expected)):
                return snapshot
            await asyncio.sleep(.25)
