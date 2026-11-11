import asyncio
from types import SimpleNamespace

import pytest
from stockwidget.config import Config, Store
from stockwidget.mcp_depth import DepthSnapshot
from stockwidget.mcp_selftest import McpSelfTest
from stockwidget.webui import SettingsServer


def test_routes_require_token_before_starting_or_changing_display(tmp_path, monkeypatch):
    server = SettingsServer(Store(tmp_path/'config.json'), lambda _: None)
    server.app.config['TESTING'] = True
    calls = []
    monkeypatch.setattr(server.mcp_test, 'start', lambda cfg: calls.append(cfg))
    client = server.app.test_client()
    assert 403 == client.post('/api/mcp-test').status_code
    assert 403 == client.get('/api/mcp-test').status_code
    assert [] == calls
    server.store.update(dict(display_theme='theme3', show_chart_depth=False, show_sparkline=False,
        symbols=['603986', '000001'], stock_visibility={'603986': {'chart': False, 'name': False},
                                                       '000001': {'chart': False}}))
    result = client.post('/api/mcp-test?token='+server.token)
    assert 202 == result.status_code
    assert calls[0].show_chart_depth and calls[0].show_sparkline
    assert 'theme3' == calls[0].display_theme
    assert calls[0].stock_component_visible('603986', 'chart')
    assert not calls[0].stock_component_visible('603986', 'name')
    assert not calls[0].stock_component_visible('000001', 'chart')


def test_display_pass_requires_matching_actual_paint_receipt(tmp_path):
    server = SettingsServer(Store(tmp_path/'config.json'), lambda _: None)
    server.app.config['TESTING'] = True
    depth = DepthSnapshot('603986', source_at='2026-11-11T15:00:00+08:00')
    server.mcp_test._update(depth=dict(symbol=depth.symbol, source_at=depth.source_at), delivered_at=0)
    server.publish_depth_received(depth)
    client = server.app.test_client()
    url = '/api/mcp-test?token='+server.token
    assert 'pending' == client.get(url).json['display']['status']
    server.publish_depth_painted(DepthSnapshot('603986', source_at='old'), True)
    assert 'pending' == client.get(url).json['display']['status']
    server.publish_depth_painted(depth, False)
    assert 'warn' == client.get(url).json['display']['status']
    server.publish_depth_painted(depth, True)
    assert 'pass' == client.get(url).json['display']['status']


def test_step_failure_identifies_stage_without_claiming_pass():
    job = McpSelfTest()
    async def fail():
        raise RuntimeError('source unavailable')
    with pytest.raises(RuntimeError):
        asyncio.run(job.step('真实千档采集', fail))
    value = job.snapshot()
    assert 'fail' == value['steps'][0]['status']
    assert 'source unavailable' in value['steps'][0]['detail']


def test_step_cancellation_records_failure_without_masking_timeout():
    job = McpSelfTest()
    async def wait():
        await asyncio.Event().wait()
    async def scenario():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(job.step('服务器事件回传', wait), .01)
    asyncio.run(scenario())
    assert 'fail' == job.snapshot()['steps'][0]['status']


def test_missing_credentials_does_not_issue_network_requests():
    job = McpSelfTest()
    with pytest.raises(ValueError, match='API Key'):
        asyncio.run(job.exercise(Config(symbols=['603986'], mcp_api_key='')))


def test_selftest_controls_are_on_settings_page(tmp_path):
    server = SettingsServer(Store(tmp_path/'config.json'), lambda _: None)
    body = server.app.test_client().get('/?token='+server.token).get_data(as_text=True)
    assert '一键测试并显示千档' in body
    assert 'mcp_test.js' in body


def test_event_connection_failure_does_not_skip_independent_depth_test(monkeypatch):
    from contextlib import asynccontextmanager
    from stockwidget import mcp_selftest
    captured = []
    @asynccontextmanager
    async def failed_connection(*args):
        raise TimeoutError('discover timed out')
        yield
    async def book_test(session, config, code):
        captured.append((session, code))
    monkeypatch.setattr(mcp_selftest, 'connection', failed_connection)
    job = McpSelfTest()
    monkeypatch.setattr(job, 'book_test', book_test)
    asyncio.run(job.exercise(Config(symbols=['603986'], mcp_api_key='test',
        mcp_realtime_url='http://localhost:8802/mcp')))
    assert [(None, '603986')] == captured
    assert 'discover timed out' in job.snapshot()['error']
