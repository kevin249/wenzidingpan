"""Connection failures must stay inside workers; stopping must cancel in-flight I/O."""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import MCPError

from stockwidget.config import Config
from stockwidget.mcp_depth import McpDepthPoller
from stockwidget.mcp_notifications import McpNotificationListener, _describe_error
from stockwidget.mcp_runtime import McpConnection

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup


@pytest.mark.parametrize("worker_type,operation", [
    (McpDepthPoller, "_fetch_cycle"),
    (McpNotificationListener, "_listen"),
])
def test_stop_interrupts_an_inflight_connection(worker_type, operation, monkeypatch):
    worker = worker_type(Config(
        mcp_api_key="test-key", mcp_notifications_enabled=True, display_theme="theme2",
    ))
    started = threading.Event()
    cleaned = threading.Event()
    state = {}

    async def stuck(*args, **kwargs):
        state["loop"] = asyncio.get_running_loop()
        state["release"] = asyncio.Event()
        started.set()
        try:
            await state["release"].wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(worker, operation, stuck)
    worker.start()
    try:
        assert started.wait(2), "worker never entered the connection"
        worker.stop()
        stopped = worker.wait(1000)
    finally:
        # Clean up even when testing an implementation that cannot cancel I/O.
        worker.stop()
        if worker.isRunning() and state:
            state["loop"].call_soon_threadsafe(state["release"].set)
        assert worker.wait(3000)
    assert stopped, "stop() left network I/O alive"
    assert cleaned.is_set()


@pytest.mark.parametrize("worker_type,operation", [
    (McpDepthPoller, "_fetch_cycle"),
    (McpNotificationListener, "_listen"),
])
def test_exception_group_and_server_error_reconnect_without_ending_worker(
    worker_type, operation, monkeypatch,
):
    import stockwidget.mcp_depth as depth
    import stockwidget.mcp_notifications as notifications

    monkeypatch.setattr(depth, "DEPTH_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(notifications, "RECONNECT_SECONDS", 0.01)
    worker = worker_type(Config(
        mcp_api_key="test-key", mcp_notifications_enabled=True, display_theme="theme2",
    ))
    attempts = []

    async def fail_then_recover(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [
                ConnectionError("connection reset"),
            ])
        if len(attempts) == 2:
            raise MCPError(-32603, "Server returned an error response")
        worker.stop()
        return True

    monkeypatch.setattr(worker, operation, fail_then_recover)
    worker.start()
    try:
        stopped = worker.wait(2000)
    finally:
        worker.stop()
        assert worker.wait(3000)
    assert stopped
    assert len(attempts) == 3


def test_cancel_tolerates_a_loop_closed_during_the_cross_thread_handoff():
    connection = McpConnection()
    loop = asyncio.new_event_loop()
    loop.close()
    connection._loop = loop
    connection._task = SimpleNamespace(cancel=lambda: None)
    connection.cancel()


@pytest.mark.parametrize("worker_type,operation", [
    (McpDepthPoller, "_fetch_cycle"),
    (McpNotificationListener, "_listen"),
])
def test_config_change_cancels_old_connection_and_reconnects(worker_type, operation, monkeypatch):
    config = Config(mcp_api_key="old-key", mcp_notifications_enabled=True, display_theme="theme2")
    worker = worker_type(config)
    started = threading.Event()
    keys = []
    state = {}

    async def connect(current, **kwargs):
        keys.append(current.mcp_api_key)
        if len(keys) == 1:
            state["loop"] = asyncio.get_running_loop()
            state["release"] = asyncio.Event()
            started.set()
            await state["release"].wait()
        else:
            worker.stop()
        return False

    monkeypatch.setattr(worker, operation, connect)
    worker.start()
    try:
        assert started.wait(2)
        worker.apply_config(replace(config, mcp_api_key="new-key"))
        stopped = worker.wait(1000)
    finally:
        worker.stop()
        if worker.isRunning() and state:
            try:
                state["loop"].call_soon_threadsafe(state["release"].set)
            except RuntimeError:
                pass
        assert worker.wait(3000)
    assert stopped
    assert keys == ["old-key", "new-key"]


def test_error_description_unwraps_groups_and_keeps_mcp_error_code():
    error = ExceptionGroup("unhandled errors in a TaskGroup", [
        MCPError(-32603, "Server returned an error response"),
        ExceptionGroup("nested", [ConnectionError("connection reset")]),
    ])
    description = _describe_error(error)
    assert "MCPError(code=-32603)" in description
    assert "ConnectionError: connection reset" in description
    assert "unhandled errors in a TaskGroup" not in description


def test_app_survives_repeated_mcp_failures_and_quits_with_io_inflight():
    """Run real Qt threads in a child process so a QThread abort is observable."""
    script = '''
import asyncio
import tempfile
from pathlib import Path
from PySide6.QtCore import QTimer
from stockwidget import mcp_depth, mcp_notifications
from stockwidget.app import WidgetApp
from stockwidget.config import Store
from mcp.shared.exceptions import MCPError
try:
    ExceptionGroup
except NameError:
    from exceptiongroup import ExceptionGroup

mcp_depth.DEPTH_RETRY_SECONDS = 0.01
mcp_notifications.RECONNECT_SECONDS = 0.01
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / "config.json")
    store.update({"provider": "mock", "symbols": ["603986"],
                  "mcp_api_key": "test-key", "mcp_notifications_enabled": True,
                  "display_theme": "theme2", "show_sparkline": False,
                  "show_dark_trade": False, "intraday_chart": False})
    app = WidgetApp([], store=store)
    attempts = {"depth": 0, "reminders": 0}
    ticks = []
    expired = []

    async def connection(name):
        attempts[name] += 1
        if attempts[name] == 1:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("reset")])
        if attempts[name] == 2:
            raise MCPError(-32603, "Server returned an error response")
        await asyncio.Event().wait()

    app.depth_poller._fetch_cycle = lambda *args, **kwargs: connection("depth")
    app.notification_listener._listen = lambda *args: connection("reminders")
    timer = QTimer()
    def check():
        ticks.append(True)
        if min(attempts.values()) >= 3:
            timer.stop()
            app.quit()
    timer.timeout.connect(check)
    timer.start(10)
    def watchdog():
        expired.append(True)
        app.quit()
    QTimer.singleShot(5000, watchdog)
    assert app.run() == 0
    assert not expired, (attempts, "reconnection stalled")
    assert ticks and min(attempts.values()) == 3, attempts
    assert not any(worker.isRunning() for worker in app._workers())
    print("MCP_FAILURE_SURVIVAL_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MCP_FAILURE_SURVIVAL_OK" in result.stdout


def test_real_sdk_errors_do_not_stop_the_qt_app():
    script = '''
import json
import tempfile
from pathlib import Path
import httpx2
from PySide6.QtCore import QTimer
from stockwidget import mcp_depth, mcp_notifications
from stockwidget.app import WidgetApp
from stockwidget.config import Store

calls = []
async def gateway(request):
    if request.method != "POST":
        return httpx2.Response(405)
    payload = json.loads(request.content)
    calls.append(payload["method"])
    return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"],
        "error": {"code": -32603, "message": "Server returned an error response"}})

original_client = httpx2.AsyncClient
def client(**kwargs):
    return original_client(transport=httpx2.MockTransport(gateway), **kwargs)
httpx2.AsyncClient = client
mcp_depth.DEPTH_RETRY_SECONDS = 0.01
mcp_notifications.RECONNECT_SECONDS = 0.01
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory) / "config.json")
    store.update({"provider": "mock", "symbols": ["603986"],
                  "mcp_url": "https://test.invalid/mcp", "mcp_api_key": "test-key",
                  "mcp_notifications_enabled": True, "display_theme": "theme2",
                  "show_sparkline": False, "show_dark_trade": False,
                  "intraday_chart": False})
    app = WidgetApp([], store=store)
    timer = QTimer()
    expired = []
    def check():
        if calls.count("initialize") >= 3 and calls.count("server/discover") >= 3:
            timer.stop()
            app.quit()
    timer.timeout.connect(check)
    timer.start(10)
    def watchdog():
        expired.append(True)
        app.quit()
    QTimer.singleShot(5000, watchdog)
    assert app.run() == 0
    assert not expired, calls
    assert not any(worker.isRunning() for worker in app._workers())
    print("REAL_SDK_FAILURE_SURVIVAL_OK")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REAL_SDK_FAILURE_SURVIVAL_OK" in result.stdout
