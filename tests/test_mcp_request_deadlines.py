"""B/S 与千档各自计时；慢 B/S 不能延后盘口发布。"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from stockwidget import mcp_depth, mcp_notifications
from stockwidget.config import Config


@pytest.fixture
def mocked_transport(monkeypatch):
    @asynccontextmanager
    async def client(**kwargs):
        yield object()
    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None
    @asynccontextmanager
    async def session(*args, **kwargs):
        yield object()
    async def negotiate(*args):
        return True
    monkeypatch.setattr(mcp_depth.httpx2, 'AsyncClient', client)
    monkeypatch.setattr(mcp_depth, 'streamable_http_client', transport)
    monkeypatch.setattr(mcp_depth, 'ClientSession', session)
    monkeypatch.setattr(mcp_depth, 'negotiate_protocol', negotiate)


def test_depth_and_bs_use_independent_timeouts(monkeypatch):
    calls = []

    async def bounded(coro, *, timeout_seconds=None):
        calls.append(timeout_seconds or mcp_depth.REQUEST_TIMEOUT_SECONDS)
        return await coro

    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(is_error=False, structured_content={})

    monkeypatch.setattr(mcp_depth, '_bounded', bounded)
    monkeypatch.setattr(mcp_depth, 'record_volatility_bs', lambda _: None)

    async def check():
        await mcp_depth._fetch_requested_depth(Session(), '603986', 1000)
        await mcp_depth._fetch_volatility_bs(Session(), '603986')

    asyncio.run(check())
    assert [mcp_depth.REQUEST_TIMEOUT_SECONDS,
            mcp_depth.BS_REQUEST_TIMEOUT_SECONDS] == calls
    assert calls[0] != calls[1]


def test_explicit_timeout_cancels_only_its_request():
    cancelled = []

    async def stuck():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def check():
        with pytest.raises(TimeoutError):
            await mcp_notifications._bounded(stuck(), timeout_seconds=.01)

    asyncio.run(check())
    assert [True] == cancelled


def test_depth_publishes_before_slow_bs_finishes(monkeypatch, mocked_transport):
    config = Config(symbols=['sh603986'], mcp_api_key='test')
    poller = mcp_depth.McpDepthPoller(config, sync_bs=False)
    bs_poller = mcp_depth.McpBsPoller(config)
    events = []
    poller.depth_ready.connect(lambda _: events.append('depth'))
    bs_poller.bs_ready.connect(lambda _: events.append('bs'))

    async def depth(*_):
        return mcp_depth.DepthSnapshot(symbol='603986', full_depth=True,
                                       available=True, depth_mode=mcp_depth.DEPTH_FULL)

    async def bs(*_, **kwargs):
        await asyncio.sleep(.03)
        assert ['depth'] == events
        return {}

    monkeypatch.setattr(mcp_depth, '_fetch_depth_with_fallback', depth)
    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', bs)

    async def check():
        await asyncio.gather(poller._fetch_cycle(config, fetch_bs=False, force_depth=True),
                             bs_poller._fetch_cycle(config, fetch_bs=True, fetch_depth=False))

    asyncio.run(check())
    assert ['depth', 'bs'] == events


def test_bs_timeout_does_not_count_as_a_thousand_failure(monkeypatch, mocked_transport):
    poller = mcp_depth.McpBsPoller(Config(symbols=['sh603986'], mcp_api_key='test'))
    errors = []
    poller.status_changed.connect(errors.append)

    async def timeout(*_, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', timeout)
    asyncio.run(poller._fetch_cycle(poller._config, fetch_bs=True, fetch_depth=False))
    assert {} == poller._full_depth_failures
    assert 1 == len(errors)
    assert 'B/S 603986' in errors[0]
    assert 'TimeoutError' in poller._bs_errors['603986']


def test_missing_today_bs_is_not_reported_as_a_depth_failure(monkeypatch, mocked_transport):
    poller = mcp_depth.McpBsPoller(Config(symbols=['sh603986'], mcp_api_key='test'))

    async def missing(*_, **kwargs):
        return {'ok': True, 'available': False, 'message': 'today has no result'}

    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', missing)
    asyncio.run(poller._fetch_cycle(poller._config, fetch_bs=True, fetch_depth=False))
    assert {} == poller._full_depth_failures
    assert 'today has no result' in poller._bs_errors['603986']
