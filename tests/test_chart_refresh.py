"""图表刷新失败保持画面；经典主题挂单叠加层独立开关。"""

import os
from dataclasses import replace
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from stockwidget.config import Config, Store, sanitize
from stockwidget.intraday import IntradayClient, Trend
from stockwidget.market_hours import SHANGHAI_TZ
from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
from stockwidget.poller import Poller, Snapshot
from stockwidget.providers.base import Quote
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.sparkline import Sparkline
from stockwidget.webui import SettingsServer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_depth_switch_is_validated_persisted_and_theme_scoped(tmp_path):
    assert sanitize({}).show_chart_depth
    assert sanitize({"show_chart_depth": "false"}).show_chart_depth
    store = Store(tmp_path / "config.json")
    changes = []
    server = SettingsServer(store, changes.append)
    client = server.app.test_client()
    page = client.get(f"/?token={server.token}").get_data(as_text=True)
    assert 'id="show_chart_depth" name="show_chart_depth" type="checkbox"' in page
    assert "在 K 线内显示千档挂单分布" in page
    result = client.post(f"/api/config?token={server.token}", json={"show_chart_depth": False})
    assert result.get_json()["config"]["show_chart_depth"] is False
    assert not changes[-1].show_chart_depth
    assert store.update({"display_theme": "theme2"}).show_chart_depth
    assert not store.update({"display_theme": "theme1"}).show_chart_depth
    assert not Store(store.path).get().show_chart_depth
    poller = Poller(Config())
    poller._explicit = False
    poller.apply_config(Config(show_chart_depth=False))
    assert not poller._explicit


def test_depth_switch_hides_only_the_overlay_and_restores_latest_snapshot(app, monkeypatch):
    row = QuoteRow("600000")
    config = Config(row_style="stacked")
    quote = Quote.from_prices("600000", "测试股票", 10.2, 10.0)
    trend = Trend(prices=[10.0, 10.1, 10.2], prev_close=10.0)
    depth = DepthSnapshot(symbol="600000", available=True, levels=(DepthLevel("bid", 10.1, 1000),))
    try:
        row.apply_config(config)
        row.update_quote(quote, config, trend)
        row.update_depth(depth)
        row.resize(400, 120)
        row.show()
        app.processEvents()
        calls = []
        monkeypatch.setattr(Sparkline, "_draw_depth", lambda *args: calls.append(True))
        row.sparkline.grab()
        assert calls
        calls.clear()
        row.apply_config(replace(config, show_chart_depth=False))
        app.processEvents()
        size = row.sparkline.size()
        row.sparkline.grab()
        assert not calls
        assert row.sparkline.points == trend.prices and row.sparkline.isVisible()
        latest = replace(depth, bid_count=2)
        row.update_depth(latest)
        row.sparkline.grab()
        assert not calls and row.theme2_depth._depth is latest
        row.apply_config(config)
        row.sparkline.grab()
        assert calls and row.sparkline._depth is latest
        assert row.sparkline.size() == size
    finally:
        row.close()


def test_snapshot_refresh_failure_keeps_curve_timeline_and_axis(app, monkeypatch):
    from stockwidget.ui.window import TickerWindow

    config = Config(symbols=["600000"], show_dark_trade=False)
    client = IntradayClient()
    now = datetime.now(SHANGHAI_TZ).timestamp()
    good = Trend(prices=[10.0, 10.2], minutes=["09:30", "09:31"], prev_close=10.0)
    responses = iter([good, Trend(error="请求超时"), Trend(prices=[10.0, 10.2, 10.3])])
    monkeypatch.setattr(client, "_load", lambda symbol: next(responses))
    clock = [now]
    quote = Quote.from_prices("600000", "测试股票", 10.2, 10.0)
    window = TickerWindow(config)
    try:
        for index in range(3):
            snapshot = Snapshot("mock", [quote])
            snapshot.trends[quote.symbol] = client.fetch(quote.symbol, now=clock[0])
            window.update_snapshot(snapshot, "测试")
            row = window._rows["600000"]
            row.sparkline.grab()
            if index == 0:
                bounds = row.sparkline._price_scale._bounds
            elif index == 1:
                assert row.sparkline.points == good.prices
                assert row.sparkline._minutes == good.minutes
                assert row.sparkline._price_scale._bounds == bounds
                assert "分时刷新失败" in row.sparkline.toolTip()
            else:
                assert row.sparkline.points == [10.0, 10.2, 10.3]
                assert row.sparkline.toolTip() == ""
            clock[0] += 2.0
    finally:
        window.close()


def test_quote_failure_keeps_today_but_clears_yesterdays_curve(app):
    row = QuoteRow("600000")
    config = Config()
    try:
        row.apply_config(config)
        row.update_quote(Quote.from_prices("600000", "测试", 10.2, 10.0), config,
                         Trend(prices=[10.0, 10.2], prev_close=10.0))
        row.update_quote(Quote.failed("600000", "请求超时"), config)
        assert row.sparkline.points == [10.0, 10.2]
        assert row.theme2_depth._trend.prices == [10.0, 10.2]
        assert "请求超时" in row.price_label.text()
        row._chart_day = datetime.now(SHANGHAI_TZ).date() - timedelta(days=1)
        row.update_quote(Quote.failed("600000", "请求超时"), config)
        assert row.sparkline.points == []
        assert row.theme2_depth._trend is None
    finally:
        row.close()


def test_quote_error_does_not_block_independent_intraday_fetch(monkeypatch):
    client = IntradayClient()
    trend = Trend(prices=[10.0, 10.2])
    monkeypatch.setattr(client, "_load", lambda symbol: trend)
    snapshot = Snapshot("mock", [Quote.failed("600000", "报价失败")])
    snapshot.trends["600000"] = client.fetch("600000")
    assert snapshot.trends["600000"].prices == trend.prices


@pytest.mark.parametrize("theme", ["theme1", "theme2"])
def test_partial_snapshot_updates_only_changed_row_without_rebuilding_layout(app, monkeypatch, theme):
    from stockwidget.ui.window import TickerWindow

    config = Config(display_theme=theme, symbols=["600000", "603986"])
    window = TickerWindow(config)
    quotes = [Quote.from_prices(s, s, 10.1, 10) for s in config.symbols]
    try:
        window.update_snapshot(Snapshot("test", quotes), "test")
        sibling = window._rows["603986"]
        old_quote = sibling._last_quote
        updated = Quote.from_prices("600000", "test", 10.5, 10)
        trend = Trend(prices=[10, 10.5])

        def unexpected(*args, **kwargs):
            raise AssertionError("a partial update must not rebuild every row")

        monkeypatch.setattr(window, "_sync_rows", unexpected)
        monkeypatch.setattr(sibling, "update_quote", unexpected)
        window.update_snapshot(Snapshot(
            "test", [updated, quotes[1]], trends={"600000": trend},
            updated_symbols=("600000",),
        ), "test")
        assert window._rows["600000"]._last_quote.price == 10.5
        assert window._rows["600000"]._last_trend is trend
        assert sibling._last_quote is old_quote
        assert set(window._rows) == set(config.symbols)
    finally:
        window.close()


def test_real_queued_gui_receives_fast_data_before_blocked_chart(app, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace
    from stockwidget import poller as module
    from stockwidget.app import WidgetApp
    from stockwidget.ui.window import TickerWindow

    config = Config(display_theme="theme2", symbols=["600000", "603986"],
                    show_dark_trade=False, refresh_seconds=0.1, chart_refresh_seconds=0.1)
    window = TickerWindow(config)
    poller = Poller(config)
    release = threading.Event()
    monkeypatch.setattr(module, "active_updates_allowed", lambda *args: True)
    monkeypatch.setattr(poller, "_fetch_quotes", lambda cfg:
                        ("test", None, [Quote.from_prices(s, s, 10.5, 10) for s in cfg.symbols]))

    def chart(client, symbol):
        if symbol == "603986":
            release.wait(2)
        return Trend(prices=[10, 10.5])

    monkeypatch.setattr(poller, "_fetch_chart", chart)
    stub = SimpleNamespace(poller=poller, _on_snapshot=lambda snapshot: window.update_snapshot(snapshot, "test"))
    poller.snapshot_ready.connect(lambda: WidgetApp._on_poll_ready(stub), Qt.QueuedConnection)
    window.show()
    poller.start()
    try:
        until = time.monotonic() + 1.5
        while time.monotonic() < until:
            app.processEvents()
            row = window._rows.get("600000")
            if row and row._last_trend and row._last_quote.price == 10.5:
                break
            time.sleep(0.005)
        assert set(window._rows) == set(config.symbols)
        row = window._rows["600000"]
        window.grab()  # Exercise paint after the real queued cross-thread delivery.
        assert row.theme2_depth._price == 10.5
        assert row.theme2_depth._trend.prices == [10, 10.5]
        assert window._rows["603986"]._last_trend is None
        assert not release.is_set()
    finally:
        release.set()
        poller.stop()
        assert poller.wait(2000)
        window.close()
