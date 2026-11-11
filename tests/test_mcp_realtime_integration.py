"""实时客户端与设置、旧轮询器和 B/S 图层之间的回归测试。"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
import queue
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stockwidget import mcp_bs, mcp_depth, mcp_realtime
from stockwidget.config import Config, Store, sanitize
from stockwidget.mcp_depth import McpDepthPoller
from stockwidget.mcp_notifications import McpNotificationListener
from stockwidget.mcp_realtime_depth import DepthUpdates, DepthWorker
from stockwidget.webui import SettingsServer

URL = 'http://127.0.0.1:8802/mcp'


def test_settings_save_restore_and_clear_realtime_url(tmp_path):
    applied = []
    store = Store(tmp_path / 'config.json')
    server = SettingsServer(store, on_change=applied.append)
    server.app.config['TESTING'] = True
    client = server.app.test_client()
    endpoint = f'/api/config?token={server.token}'
    body = client.get(f'/?token={server.token}').get_data(as_text=True)
    assert 'id="mcp_realtime_url"' in body
    result = client.post(endpoint, json={'mcp_realtime_url': URL}).get_json()
    assert URL == result['config']['mcp_realtime_url']
    assert URL == applied[-1].mcp_realtime_url
    assert URL == Store(store.path).get().mcp_realtime_url
    store.update({'display_theme': 'theme2'})
    assert URL == store.get().mcp_realtime_url
    client.post(endpoint, json={'mcp_realtime_url': ''})
    assert '' == Store(store.path).get().mcp_realtime_url


@pytest.mark.parametrize('value', ['file:///secret', 'https://', 123, 'x' * 2049])
def test_invalid_realtime_url_does_not_enable_realtime_mode(value):
    assert '' == sanitize({'mcp_realtime_url': value}).mcp_realtime_url


def test_switching_realtime_mode_wakes_old_poller_and_disables_full_queries():
    legacy = Config(mcp_api_key='test-key', symbols=['603986'], display_theme='theme2')
    realtime = replace(legacy, mcp_realtime_url=URL)
    poller = McpDepthPoller(legacy)
    poller.apply_config(realtime)
    assert poller._wake.is_set()
    assert not poller._enabled(realtime)
    poller._wake.clear()
    poller.apply_config(legacy)
    assert poller._wake.is_set()
    assert poller._enabled(legacy)
    assert McpNotificationListener._identity(legacy) != McpNotificationListener._identity(realtime)


def test_late_legacy_response_cannot_overwrite_realtime_cache(monkeypatch):
    legacy = Config(mcp_api_key='test-key', symbols=['603986'])
    poller = McpDepthPoller(legacy)
    stored, published = [], []
    monkeypatch.setattr(mcp_depth, 'record_volatility_bs', stored.append)
    poller.bs_ready.connect(published.append)
    poller.depth_ready.connect(published.append)
    class Session:
        async def call_tool(self, *args, **kwargs):
            poller.apply_config(replace(legacy, mcp_realtime_url=URL))
            return SimpleNamespace(is_error=False, structured_content={'available': True})
    asyncio.run(mcp_depth._fetch_volatility_bs(Session(), '603986', store=poller._store_legacy_bs))
    poller._publish_depth('603986', mcp_depth.DepthSnapshot(symbol='603986'), 1)
    assert [] == stored
    assert [] == published
    assert {} == poller._depth_modes


def _listener():
    return SimpleNamespace(bs_updated=SimpleNamespace(emit=lambda symbol: None))


def _event(revision, *, status='active', price='100.00'):
    return dict(event_id=f'event-{revision}', signal_id='signal-one', revision=revision,
        status=status, event_type='trading.holding_t_signal', symbol='603986.SH', name='兆易创新',
        event='B', price=price, occurred_at='2026-11-11T10:00:00+08:00',
        generated_at='2026-11-11T10:00:01+08:00', sequence=revision,
        alert_until='2026-11-11T10:02:01+08:00', l2_confirmation='confirmed')


def test_realtime_revisions_replace_chart_marker_and_invalidations_remove_it():
    mcp_bs.reset_for_tests()
    try:
        listener = _listener()
        now = datetime(2026, 11, 11, 14, tzinfo=ZoneInfo('Asia/Shanghai'))
        bars = [SimpleNamespace(time='2026-11-11')]
        mcp_realtime.emit_event(listener, _event(1), alert=False)
        assert [(30, 'B')] == mcp_bs.bs_points('603986', [100.] * 242, now)
        mcp_realtime.emit_event(listener, dict(_event(2), event='S'), alert=False)
        # 较旧修订晚到也不能把图层恢复成 B。
        mcp_realtime.emit_event(listener, dict(_event(1), event_id='late-one'), alert=False)
        assert [(30, 'S')] == mcp_bs.bs_points('603986', [100.] * 242, now)
        assert [(0, 'S')] == mcp_bs.daily_bs_points('603986', bars)
        mcp_realtime.emit_event(listener, _event(3, status='invalidated'), alert=False)
        assert [] == mcp_bs.bs_points('603986', [100.] * 242, now)
        assert [] == mcp_bs.daily_bs_points('603986', bars)
        assert 4 == len(mcp_bs.today_records(now))
    finally:
        mcp_bs.reset_for_tests()


def test_depth_updates_coalesce_by_symbol_and_preserve_fair_order():
    updates = DepthUpdates()
    for _ in range(100):
        updates.put_nowait('603986')
    updates.put_nowait('600000')
    assert '603986' == updates.get_nowait()
    assert '600000' == updates.get_nowait()
    with pytest.raises(queue.Empty):
        updates.get_nowait()


def test_failed_depth_request_stays_pending_for_reconnect(monkeypatch):
    async def fail(*args):
        raise OSError('connection lost')
    monkeypatch.setattr(mcp_realtime, 'emit_depth', fail)
    worker = DepthWorker(_listener(), Config())
    worker.updates.put_nowait('603986')
    async def scenario():
        worker.loop = asyncio.get_running_loop()
        with pytest.raises(OSError):
            await worker._consume(SimpleNamespace())
    asyncio.run(scenario())
    assert '603986' == worker.updates.get_nowait()


def test_unavailable_depth_retries_without_another_resource_update(monkeypatch):
    calls = []
    worker = DepthWorker(_listener(), Config())
    async def receive(listener, session, symbol):
        calls.append(symbol)
        if symbol == '603986' and calls.count(symbol) == 1:
            return False
        if calls.count('603986') == 2:
            worker.stopping.set()
        return True
    monkeypatch.setattr(mcp_realtime, 'emit_depth', receive)
    monkeypatch.setattr('stockwidget.mcp_realtime_depth.RETRY_SECONDS', 0.01)
    worker.updates.put_nowait('603986')
    worker.updates.put_nowait('600000')
    async def scenario():
        worker.loop = asyncio.get_running_loop()
        await asyncio.wait_for(worker._consume(SimpleNamespace()), 1)
    asyncio.run(scenario())
    assert ['603986', '600000', '603986'] == calls


def test_cancel_during_initial_recovery_stops_immediately():
    closed = []
    async def scenario():
        cancel = asyncio.Event()
        started = asyncio.Event()
        async def recovery():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.append(True)
        task = asyncio.create_task(mcp_realtime.run_cancellable(recovery(), cancel))
        await started.wait()
        cancel.set()
        await asyncio.wait_for(task, 1)
    asyncio.run(scenario())
    assert [True] == closed


class _FakeDepthWorker:
    def __init__(self, *args):
        self.updates = DepthUpdates()

    def start(self):
        pass

    def close(self):
        pass


async def _consume_once(*args, **kwargs):
    pass


def test_listener_keeps_global_events_and_cursor_when_watchlist_changes(monkeypatch, tmp_path):
    import stockwidget.mcp_realtime_depth as depth_module
    calls, subscriptions, cursors = [], [], []
    global_event = dict(_event(1), event_type='screen.v2.done', signal_id='', symbol='',
                        event='选股完成', price=None)
    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def discover(self):
            pass
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == 'get_event_stream':
                value = dict(resource_uri='events', control_uri='control', depth_uri_template='depth/{code}')
            elif name == 'begin_event_recovery':
                cursors.append(arguments['cursor'])
                value = dict(cursor=arguments['cursor'], signals=[], recovery_token='recovery',
                             initial=not arguments['cursor'])
            else:
                value = dict(events=[global_event], next_cursor='durable', has_more=False)
            return SimpleNamespace(is_error=False, structured_content=value)
    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None
    @asynccontextmanager
    async def subscription(session, *, resource_subscriptions):
        subscriptions.append(resource_subscriptions)
        yield SimpleNamespace(honored=SimpleNamespace(resource_subscriptions=resource_subscriptions))
    monkeypatch.setattr(mcp_realtime, 'config_dir', lambda: tmp_path)
    monkeypatch.setattr(mcp_realtime, 'streamable_http_client', transport)
    monkeypatch.setattr(mcp_realtime, 'ClientSession', Session)
    monkeypatch.setattr(mcp_realtime, 'listen', subscription)
    monkeypatch.setattr(mcp_realtime, 'consume', _consume_once)
    monkeypatch.setattr(depth_module, 'DepthWorker', _FakeDepthWorker)
    config = Config(mcp_realtime_url=URL, mcp_api_key='test', symbols=['603986'])
    listener = SimpleNamespace(_emit_status=lambda *args, **kwargs: None)
    async def scenario():
        await mcp_realtime.run_listener(listener, config, asyncio.Event())
        await mcp_realtime.run_listener(listener, replace(config, symbols=['600000']), asyncio.Event())
    try:
        asyncio.run(scenario())
        assert ['', 'durable'] == cursors
        assert [['events', 'control', 'depth/603986'], ['events', 'control', 'depth/600000']] == subscriptions
        assert all(args['symbols'] == [] for name, args in calls if name != 'get_event_stream')
        assert 1 == len(mcp_bs.today_records(datetime(2026, 11, 11, 14, tzinfo=ZoneInfo('Asia/Shanghai'))))
    finally:
        mcp_bs.reset_for_tests()
