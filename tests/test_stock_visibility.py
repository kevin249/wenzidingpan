"""每股显示配置、独立开关与关闭后的自然布局。"""

import itertools
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from stockwidget.config import STOCK_COMPONENTS, Config, Store, sanitize
from stockwidget.providers.base import Quote
from stockwidget.ui.marquee import Marquee
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.window import TickerWindow
from stockwidget.webui import SettingsServer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def quote(symbol="603986"):
    result = Quote.from_prices(symbol, "兆易创新", 286, 280)
    result.dark_fund = 198_000_000
    return result


def test_visibility_defaults_validation_and_normalization():
    default = sanitize({})
    assert all(default.stock_component_visible("600519", part) for part in STOCK_COMPONENTS)
    config = sanitize({"symbols": ["603986.SH", "000001"], "stock_visibility": {
        "SH603986": {"name": False, "price": False, "percent": True, "dark": 0,
                     "chart": "false", "other": False},
        "000001": [], "600519": {"chart": False},
    }})
    assert config.stock_visibility == {"603986": {"name": False, "price": False, "percent": True}}
    assert not config.stock_component_visible("sh603986", "price")
    assert config.stock_component_visible("603986.SH", "percent")
    assert config.stock_component_visible("000001", "price")
    assert not replace(config, show_stock_price=False).stock_component_visible("603986", "percent")


def test_visibility_api_persists_across_reordering_themes_and_restart(tmp_path):
    store = Store(tmp_path / "config.json")
    changes = []
    server = SettingsServer(store, changes.append)
    client = server.app.test_client()
    url = f"/api/config?token={server.token}"
    flags = {"603986": {"chart": False, "price": False, "percent": True}}
    response = client.post(url, json={"symbols": ["603986", "000001"], "stock_visibility": flags})
    assert response.get_json()["config"]["stock_visibility"] == flags
    assert changes[-1].stock_visibility == flags
    for patch in ({"symbols": ["000001", "603986"]}, {"display_theme": "theme2"}):
        assert client.post(url, json=patch).get_json()["config"]["stock_visibility"] == flags
    assert Store(store.path).get().stock_visibility == flags
    assert store.update({"symbols": ["000001"]}).stock_visibility == {}


@pytest.mark.parametrize("style", ["sides", "stacked", "top_bottom"])
@pytest.mark.parametrize("flags", [dict(zip(STOCK_COMPONENTS, values))
                                   for values in itertools.product([False, True], repeat=5)])
def test_all_component_combinations_collapse_empty_grid_rows(app, style, flags):
    row = QuoteRow("603986.SH")
    row.resize(520, 200)
    config = Config(row_style=style, stock_visibility={"603986": flags})
    try:
        row.apply_config(config)
        row.update_quote(quote("603986.SH"), config)
        row.show()
        app.processEvents()
        widgets = (row.name_label, row.price_label, row.percent_label, row.dark_box, row.sparkline)
        used_rows = set()
        for part, widget in zip(STOCK_COMPONENTS, widgets):
            assert widget.isHidden() is not flags[part]
            index = row.layout().indexOf(widget)
            assert (index >= 0) is flags[part]
            if index >= 0:
                first, _, span, _ = row.layout().getItemPosition(index)
                used_rows.update(range(first, first + span))
        assert sorted(used_rows) == list(range(len(used_rows)))
        # 恢复全部显示，旧的行拉伸和跨行位置也应恢复。
        row.apply_config(Config(row_style=style))
        app.processEvents()
        assert all(not widget.isHidden() for widget in widgets)
    finally:
        row.close()


def test_hiding_kline_compacts_stacked_rows_and_window_height(app):
    window = TickerWindow(Config(row_style="stacked", visible_rows=2))
    try:
        window._sync_rows([quote(), quote("000001")])
        window.show()
        app.processEvents()
        window._resize_to_grid()  # 初次显示已按实际宽度切换为窄卡片，重新量自然高度。
        app.processEvents()
        initial_height = window.height()
        window.apply_config(replace(window._config, stock_visibility={"603986": {"chart": False}}))
        app.processEvents()
        row = window._rows["603986"]
        assert row.layout().getItemPosition(row.layout().indexOf(row.dark_box))[0] == 1
        assert row.dark_box.y() == row.name_label.geometry().bottom() + 1
        assert window.height() < initial_height
        assert window._rows["000001"].sparkline.isVisible()
        assert window._rows["000001"].height() >= window._rows["000001"].sizeHint().height() - 2
        assert window._scale == 1.0
        window.apply_config(replace(window._config, stock_visibility={}))
        app.processEvents()
        assert window.height() == initial_height
    finally:
        window.close()


def test_hiding_top_text_moves_kline_to_first_row(app):
    row = QuoteRow("603986")
    config = Config(row_style="stacked", stock_visibility={"603986": {"name": False, "price": False}})
    try:
        row.apply_config(config)
        row.update_quote(quote(), config)
        assert row.layout().getItemPosition(row.layout().indexOf(row.sparkline))[0] == 0
        assert row.layout().getItemPosition(row.layout().indexOf(row.dark_box))[0] == 1
    finally:
        row.close()


def test_theme2_and_marquee_keep_price_and_percent_independent(app, monkeypatch):
    config = Config(display_theme="theme2", stock_visibility={
        "603986": {"name": False, "price": False, "dark": False, "chart": False}
    })
    row = QuoteRow("603986")
    marquee = Marquee()
    try:
        row.apply_config(config)
        row.update_quote(quote(), config)
        row.set_theme2_expanded(True, notify=False)
        row.show()
        app.processEvents()
        assert row.theme2_name_label.isHidden()
        assert row.theme2_dark_label.isHidden()
        canvas = row.theme2_depth
        canvas.set_quote(286, 2.14, QColor("red"), None)
        painter = FakePainter()
        canvas._draw_price_and_guide(painter, canvas._layout())
        assert painter.texts == ["+2.14%"]
        chart_calls = []
        monkeypatch.setattr(canvas, "_draw_chart_foreground", lambda *args: chart_calls.append(True))
        canvas.grab()
        assert chart_calls == []
        assert canvas._chart_rect.isEmpty()
        marquee.apply_config(config)
        marquee.set_quotes([quote()])
        assert [segment.text for segment in marquee._segments] == ["+2.14%"]
    finally:
        row.close()
        marquee.close()


class FakePainter:
    def __init__(self):
        self.texts = []

    def drawText(self, *args):
        self.texts.append(args[-1])

    def __getattr__(self, name):
        return lambda *args: None
