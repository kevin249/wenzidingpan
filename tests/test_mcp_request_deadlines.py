"""B/S 与千档各自计时；慢 B/S 不能延后盘口发布。"""
import asyncio
from types import SimpleNamespace

import pytest

from stockwidget import mcp_depth, mcp_notifications
from stockwidget.config import Config


def test_depth_and_bs_use_independent_timeouts(monkeypatch):
    calls = []

    async def bounded(coro, *, timeout_seconds):
        calls.append(timeout_seconds)
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
    assert [mcp_depth.DEPTH_REQUEST_TIMEOUT_SECONDS,
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


def test_depth_publishes_before_slow_bs_finishes(monkeypatch):
    poller = mcp_depth.McpDepthPoller(Config(symbols=['sh603986']))
    events = []
    poller.depth_ready.connect(lambda _: events.append('depth'))
    poller.bs_ready.connect(lambda _: events.append('bs'))

    async def depth(*_):
        return mcp_depth.DepthSnapshot(symbol='603986', full_depth=True,
                                       available=True, depth_mode=mcp_depth.DEPTH_FULL)

    async def bs(*_):
        await asyncio.sleep(.03)
        assert ['depth'] == events
        return {}

    monkeypatch.setattr(mcp_depth, '_fetch_depth_with_fallback', depth)
    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', bs)

    async def check():
        await asyncio.gather(poller._fetch_depth_rows(None, poller._config, force=True),
                             poller._fetch_bs_rows(None, poller._config))

    asyncio.run(check())
    assert ['depth', 'bs'] == events


def test_bs_timeout_does_not_count_as_a_thousand_failure(monkeypatch):
    poller = mcp_depth.McpDepthPoller(Config(symbols=['sh603986']))
    errors = []
    poller.status_changed.connect(errors.append)

    async def timeout(*_):
        raise TimeoutError()

    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', timeout)
    asyncio.run(poller._fetch_bs_rows(None, poller._config))
    assert {} == poller._full_depth_failures
    assert 1 == len(errors)
    assert 'B/S 603986' in errors[0]
    assert {'603986': 'timeout'} == poller._bs_errors


def test_missing_today_bs_is_not_reported_as_a_depth_failure(monkeypatch):
    poller = mcp_depth.McpDepthPoller(Config(symbols=['sh603986']))

    async def missing(*_):
        return {'ok': True, 'available': False, 'message': 'today has no result'}

    monkeypatch.setattr(mcp_depth, '_fetch_volatility_bs', missing)
    asyncio.run(poller._fetch_bs_rows(None, poller._config))
    assert {} == poller._full_depth_failures
    assert {'603986': 'today has no result'} == poller._bs_errors
