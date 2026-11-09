"""新悬浮主题保留行情缓存，设置入口使用新的主题名称。"""

import time

import pytest
from PySide6.QtWidgets import QApplication

from stockwidget.config import Config, Store
from stockwidget.mcp_depth import DEPTH_FULL, DepthLevel, DepthSnapshot
from stockwidget.providers.base import Quote
from stockwidget.ui.window import TickerWindow
from stockwidget.webui import SettingsServer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["depth", "radar"])
def test_switch_stock_and_receive_new_depth(app, theme):
    quotes = [Quote.from_prices("603986", "兆易创新", 242.3, 240),
              Quote.from_prices("600519", "贵州茅台", 1400, 1390)]
    window = TickerWindow(Config(display_theme=theme, symbols=[q.symbol for q in quotes]))
    try:
        window.restore_bounds(None, [])
        assert window.width() < 700
        window.show()
        window._sync_rows(quotes)
        app.processEvents()
        assert window._focus_symbol == "603986"
        assert not window._rows["603986"].sparkline._card_style
        assert not window._rows["600519"].isVisible()

        window._select_focus("600519")
        app.processEvents()
        assert window.stealth.peek.isVisible()
        assert window.stealth.selected_symbol == "600519"
        assert not window._rows["600519"].isVisible()
        assert not window._rows["603986"].isVisible()

        depth = DepthSnapshot(symbol="600519", available=True, full_depth=True,
                              depth_mode=DEPTH_FULL, received_at=time.time(),
                              levels=(DepthLevel("bid", 1399.99, 100),))
        window.update_depth(depth)
        assert window._rows["600519"].sparkline._depth is depth
        chart = window._rows["600519"].sparkline
        chart.set_series([1399.98, 1400, 1400.02], ["09:30", "09:31", "09:32"])
        newer = DepthSnapshot(symbol="600519", available=True, full_depth=True,
                              depth_mode=DEPTH_FULL, received_at=time.time(),
                              levels=(DepthLevel("bid", 1399.99, 300),
                                      DepthLevel("ask", 1400.01, 150)))
        window.update_depth(newer)
        app.processEvents()
        assert chart._depth is newer
        assert chart._split_depth
    finally:
        window.close()


def test_new_theme_profiles_and_web_choices(tmp_path):
    store = Store(tmp_path / "config.json")
    store.update({"display_theme": "depth", "chart_depth_width": 200})
    store.update({"display_theme": "radar", "chart_depth_width": 300})
    assert store.update({"display_theme": "depth"}).chart_depth_width == 200
    assert store.update({"display_theme": "radar"}).chart_depth_width == 300
    server = SettingsServer(store, lambda _: None)
    page = server.app.test_client().get(f"/?token={server.token}").get_data(as_text=True)
    assert "一行文字" in page and "贴边小列" in page
    assert 'value="theme1"' not in page and 'value="theme2"' not in page
