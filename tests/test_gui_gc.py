"""Cyclic finalizers must not run in quote/TDW workers during Qt painting."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def run_child(script, *args):
    result = subprocess.run(
        [sys.executable, '-c', script, *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize('guarded', [False, True])
def test_qobject_cycle_is_collected_on_gui_thread_instead_of_allocating_worker(guarded):
    output = run_child('''
import gc, threading, sys
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from stockwidget.gui_gc import GuiGarbageCollector
app = QApplication([])
guarded = sys.argv[1] == 'True'
gc.disable()
released = []
class Holder:
    def __init__(self):
        self.object = QObject()
        self.cycle = self
    def __del__(self):
        released.append(threading.current_thread().name)
holder = Holder()
del holder
collector = GuiGarbageCollector(app, interval_ms=10) if guarded else None

def allocate():
    if not guarded:
        gc.enable()
    for _ in range(10000):
        cycle = []
        cycle.append(cycle)
    gc.disable()
worker = threading.Thread(target=allocate, name='tdw-repro')
worker.start()
worker.join()
if guarded:
    assert released == [], released
    def check():
        if released:
            app.quit()
    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(10)
    QTimer.singleShot(2000, app.quit)
    app.exec()
    assert released == ['MainThread'], released
    assert not gc.isenabled(), 'late executor tasks must not regain automatic GC'
    assert not collector._timer.isActive()
else:
    assert released == ['tdw-repro'], released
print('GC_THREAD_OK', released)
''', str(guarded))
    assert 'GC_THREAD_OK' in output


@pytest.mark.parametrize('theme', ['theme1', 'theme2', 'theme3'])
def test_app_keeps_painting_with_worker_allocations_and_mcp_cache_updates(theme):
    output = run_child('''
import asyncio, gc, tempfile, threading, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from stockwidget.app import WidgetApp
from stockwidget.config import Store
from stockwidget.intraday import Trend
from stockwidget.mcp_depth import DepthSnapshot, DepthLevel
from stockwidget.providers.base import Quote

gc_threads = []
painted = []
errors = []
sys.excepthook = lambda *args: errors.append(str(args[1]))
def allocate(*args):
    for _ in range(1000):
        cycle = []
        cycle.append(cycle)
    return 'mock', None, [Quote.from_prices('603986', '测试', 100.1, 100.0)]
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / 'config.json')
    store.update({'symbols': ['603986'], 'provider': 'mock', 'display_theme': sys.argv[1],
        'debug_mode': True, 'refresh_seconds': .1, 'chart_refresh_seconds': .1,
        'show_dark_trade': False, 'mcp_notifications_enabled': True, 'mcp_api_key': 'test'})
    app = WidgetApp([], store=store)
    assert not gc.isenabled()
    app._gui_gc._timer.setInterval(20)
    app.poller._fetch_quotes = allocate
    app.poller._fetch_chart = lambda *args: Trend(
        prices=[100., 100.1, 100.2], minutes=['09:30', '09:31', '09:32'])
    from stockwidget.realtime import RealtimeFeed
    def tdw_fetch(*args):
        allocate()
        return None
    RealtimeFeed.fetch = tdw_fetch
    async def depth(*args, **kwargs):
        app.depth_poller.depth_ready.emit(DepthSnapshot('603986', available=True,
            levels=(DepthLevel('bid',100.05,1000), DepthLevel('ask',100.15,500))))
        return True
    async def listen(*args):
        while True:
            app.notification_listener.cache_updated.emit()
            await asyncio.sleep(.02)
    app.depth_poller._fetch_cycle = depth
    app.notification_listener._listen = listen
    def record(phase, info):
        if phase == 'start':
            gc_threads.append(threading.current_thread().ident)
    gc.callbacks.append(record)
    timer = QTimer()
    def paint():
        if app.window._rows:
            app.window.grab()
            painted.append(True)
    timer.timeout.connect(paint)
    timer.start(20)
    QTimer.singleShot(500, app.quit)
    assert app.run() == 0
    gc.callbacks.remove(record)
    assert not errors, errors
    assert painted and gc_threads
    assert set(gc_threads) == {threading.main_thread().ident}, gc_threads
    assert not any(worker.isRunning() for worker in app._workers())
    print('PAINT_AND_GC_OK')
''', theme)
    assert 'PAINT_AND_GC_OK' in output
