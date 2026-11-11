"""订阅心跳正常时，小查询超时不应反复拆掉整个推送连接。"""
import asyncio
from types import SimpleNamespace

import pytest
from stockwidget import mcp_realtime as realtime
from stockwidget.mcp_realtime_depth import DepthUpdates


@pytest.mark.parametrize('value', ['2026-11-11T15:46:35Z', '2026-11-11T23:46:35+08:00',
                                  '2026-11-11T23:46:35'])
def test_event_clock_displays_shanghai_time(value):
    assert '23:46:35' == realtime.event_clock(value)


@pytest.mark.parametrize('stage', ['delta', 'state'])
def test_query_timeout_retries_cursor_without_closing_live_subscription(monkeypatch, stage):
    statuses, cursors = [], []
    async def scenario():
        cancel = asyncio.Event()
        async def subscription():
            while True:
                yield SimpleNamespace(uri='control')
                await asyncio.sleep(.001)
        calls = 0
        async def drain(session, journal, cursor):
            nonlocal calls
            calls += 1
            cursors.append(cursor)
            if stage == 'delta' and calls == 1:
                raise TimeoutError('get_event_delta 超过 15 秒')
            return 'committed', []
        async def state(*args):
            if stage == 'state' and calls == 1:
                raise TimeoutError('get_stream_state 超过 15 秒')
            cancel.set()
            return dict(depth_versions={})
        monkeypatch.setattr(realtime, '_drain', drain)
        monkeypatch.setattr(realtime, '_call', state)
        listener = SimpleNamespace(_emit_status=lambda value, **kw: statuses.append(value))
        await asyncio.wait_for(realtime.consume(listener, None, subscription(), journal=None,
            cursor='durable', symbols=[], cancel_event=cancel, depth_updates=DepthUpdates()), 1)
    asyncio.run(scenario())
    assert ['durable', 'durable' if stage == 'delta' else 'committed'] == cursors
    assert 1 == len(statuses)
    assert '订阅心跳仍在' in statuses[0]


def test_stale_subscription_still_reconnects_after_query_timeout(monkeypatch):
    async def scenario():
        async def subscription():
            yield SimpleNamespace(uri='control')
            await asyncio.Event().wait()
        async def drain(*args):
            raise TimeoutError('get_event_delta 超过 15 秒')
        monkeypatch.setattr(realtime, '_drain', drain)
        monkeypatch.setattr(realtime, 'HEARTBEAT_GRACE_SECONDS', 0)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(realtime.consume(None, None, subscription(), journal=None,
                cursor='durable', symbols=[], cancel_event=asyncio.Event(), depth_updates=DepthUpdates()), 1)
    asyncio.run(scenario())
