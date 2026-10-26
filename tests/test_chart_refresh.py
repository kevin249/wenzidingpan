"""图表刷新失败保持画面；经典主题挂单叠加层独立开关。"""

import os
from dataclasses import replace
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from stockwidget.config import Config, Store, sanitize
from stockwidget.intraday import Trend
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
    poller = Poller(config)
    now = datetime.now(SHANGHAI_TZ).timestamp()
    good = Trend(prices=[10.0, 10.2], minutes=["09:30", "09:31"], prev_close=10.0)
    responses = iter([good, Trend(error="请求超时"), Trend(prices=[10.0, 10.2, 10.3])])
    monkeypatch.setattr(poller._intraday, "_load", lambda symbol: next(responses))
    clock = [now]
    fetch = poller._intraday.fetch
    monkeypatch.setattr(poller._intraday, "fetch", lambda symbol: fetch(symbol, now=clock[0]))
    quote = Quote.from_prices("600000", "测试股票", 10.2, 10.0)
    window = TickerWindow(config)
    try:
        for index in range(3):
            snapshot = Snapshot("mock", [quote])
            poller._attach_trends(snapshot.quotes, snapshot)
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
    poller = Poller(Config())
    trend = Trend(prices=[10.0, 10.2])
    monkeypatch.setattr(poller._intraday, "fetch", lambda symbol: trend)
    snapshot = Snapshot("mock", [Quote.failed("600000", "报价失败")])
    poller._attach_trends(snapshot.quotes, snapshot)
    assert snapshot.trends["600000"] is trend
