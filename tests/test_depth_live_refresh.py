"""Exercise the real polling -> queued Qt signal -> theme 3 paint path."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('protocol', ['modern', 'legacy'])
def test_theme3_depth_keeps_painting_while_bs_request_is_stuck(protocol):
    script = r'''
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
from mcp.shared.exceptions import MCPError
from PySide6.QtCore import QTimer
from stockwidget import mcp_depth
from stockwidget.app import WidgetApp
from stockwidget.config import Store
from stockwidget.providers.base import Quote

protocol = sys.argv[1]
requests = []
bs_started = threading.Event()
bs_cancelled = threading.Event()
@asynccontextmanager
async def client(**kwargs):
    yield object()
@asynccontextmanager
async def transport(*args, **kwargs):
    yield None, None
class Session:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def discover(self):
        requests.append('discover')
        if protocol == 'legacy':
            raise MCPError(-32601, 'Method not found')
    async def initialize(self):
        requests.append('initialize')
        if protocol == 'modern':
            raise MCPError(-32601, 'Use server/discover')
    async def call_tool(self, name, arguments):
        requests.append(name)
        if name == 'get_volatility_bs':
            bs_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                bs_cancelled.set()
        count = requests.count('get_tdx_depth')
        return SimpleNamespace(is_error=False, structured_content={
            'symbol': '603986.SH', 'available': True, 'verified': True,
            'service_ready': True, 'distribution_ready': True,
            'method': 'active_protocol_0559', 'full_depth_verified': False,
            'levels': [{'side': 'bid', 'price': 10, 'volume': count * 100},
                       {'side': 'ask', 'price': 10.1, 'volume': 1000}],
        })
mcp_depth.httpx2.AsyncClient = client
mcp_depth.streamable_http_client = transport
mcp_depth.ClientSession = Session
mcp_depth.DEPTH_POLL_SECONDS = .02
mcp_depth.DEPTH_RETRY_SECONDS = .02
mcp_depth.DEPTH_INTER_SYMBOL_GAP_SECONDS = 0
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / 'config.json')
    store.update({'symbols': ['603986'], 'display_theme': 'theme3',
                  'debug_mode': True, 'mcp_api_key': 'test', 'show_dark_trade': False})
    app = WidgetApp([], store=store)
    app.window._sync_rows([Quote.from_prices('603986', 'test', 10, 10)])
    app.window.show()
    row = app.window._rows['603986']
    images, volumes, errors, painted = [], [], [], []
    app.window.depth_painted.connect(lambda snapshot, shown: painted.append(snapshot) if shown else None)
    sys.excepthook = lambda *args: errors.append(str(args[1]))
    timer = QTimer()
    def check():
        snapshot = painted[-1] if painted else None
        if snapshot and (not volumes or snapshot.levels[0].volume != volumes[-1]):
            volumes.append(snapshot.levels[0].volume)
            images.append(app.window.grab().toImage())
        if len(volumes) >= 3 and bs_started.is_set():
            timer.stop()
            app.quit()
    timer.timeout.connect(check)
    timer.start(10)
    app.depth_poller.start()
    if hasattr(app, 'bs_poller'):
        app.bs_poller.start()
    expired = []
    def expire():
        expired.append(True)
        app.quit()
    QTimer.singleShot(2500, expire)
    try:
        app.qt.exec()
    finally:
        app._wait_for_workers()
    sys.excepthook = sys.__excepthook__
    assert not expired, ('depth stalled', requests, volumes)
    assert not errors, errors
    assert len(volumes) >= 3 and volumes == sorted(set(volumes)), volumes
    assert images[0] != images[-1], 'new depth never reached the painted frame'
    assert bs_cancelled.is_set(), 'shutdown left the BS request running'
    assert not any(worker.isRunning() for worker in app._workers())
    assert 'discover' in requests
    assert ('initialize' in requests) == (protocol == 'legacy'), requests
    print('DEPTH_REFRESH_OK')
'''
    result = subprocess.run(
        [sys.executable, '-c', script, protocol],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        capture_output=True, text=True, timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'DEPTH_REFRESH_OK' in result.stdout
