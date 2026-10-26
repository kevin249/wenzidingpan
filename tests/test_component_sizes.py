"""数字尺寸、实际尺寸反馈与原有窗口缩放相互配合。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from stockwidget.config import Config, Store, sanitize
from stockwidget.providers.base import Quote
from stockwidget.ui.window import TickerWindow
from stockwidget.webui import SettingsServer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_size_preferences_are_validated_and_preserved_per_theme(tmp_path):
    config = sanitize({"component_sizes": {
        "name": {"width": 1e6, "height": -5}, "price": {"width": True},
        "unknown": {"width": 300}, "chart": {"width": 150, "height": 90},
    }})
    assert config.component_sizes == {
        "name": {"width": 8192, "height": 0}, "chart": {"width": 150},
    }
    store = Store(tmp_path / "config.json")
    store.update({"component_sizes": {"name": {"width": 140}}})
    assert Store(store.path).get().component_sizes["name"]["width"] == 140
    assert store.update({"display_theme": "theme2"}).component_sizes == {}
    assert store.update({"display_theme": "theme1"}).component_sizes["name"]["width"] == 140


def test_numeric_regions_restore_automatic_sizes_and_remain_compressible(app):
    config = Config(row_style="top_bottom", chart_height=80, component_sizes={
        "row": {"width": 600, "height": 180}, "name": {"width": 120, "height": 32},
        "price": {"width": 100, "height": 40}, "percent": {"width": 80, "height": 30},
        "dark": {"width": 120, "height": 28}, "chart": {"width": 320},
    })
    quote = Quote.from_prices("603986", "兆易创新", 286, 281)
    quote.dark_fund = 198_000_000
    window = TickerWindow(config)
    window._sync_rows([quote])
    window.show()
    app.processEvents()
    metrics = window.layout_metrics()["regions"]
    for region, expected in {
        "row": (600, 180), "name": (120, 32), "price": (100, 40),
        "percent": (80, 30), "dark": (120, 28), "chart": (320, 80),
    }.items():
        assert (metrics[region]["width"], metrics[region]["height"]) == expected

    window.resize(330, 150)
    app.processEvents()
    row = window._rows[quote.symbol]
    assert row.width() <= window.scroll.viewport().width()
    assert row.sparkline.width() <= row.width()
    window.apply_config(Config(row_style="top_bottom"))
    app.processEvents()
    assert row.name_label.maximumWidth() == 16777215
    assert row.sparkline.maximumHeight() == 16777215
    window.close()


def test_numeric_window_size_uses_drag_path_and_titlebar_keeps_grip_visible(app):
    window = TickerWindow(Config(show_title_buttons=True))
    window.resize(360, 220)
    window.show()
    app.processEvents()
    saved = []
    window.bounds_changed.connect(saved.append)
    window.resize_from_settings({"width": 440, "height": 260})
    app.processEvents()
    assert window.size() == QSize(440, 260)
    assert saved[-1]["width"] == 440 and saved[-1]["manual_size"] is True
    assert window.grip.isVisible() and window.grip._always_visible
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(QSize(460, 280))
    window._on_grip_drag_finished()
    window._scale_timer.stop()
    window._apply_scale()
    app.processEvents()
    assert window.layout_metrics()["regions"]["window"]["width"] == 460
    assert window.grip.x() == window.width() - window.grip.width() - 2
    window.apply_config(Config(show_title_buttons=False))
    assert not window.grip._always_visible
    window.apply_config(Config(click_through=True))
    assert window.grip.isHidden()
    window.close()


def test_layout_api_publishes_snapshot_and_queues_validated_resize(tmp_path):
    requested = []
    server = SettingsServer(Store(tmp_path / "config.json"), lambda config: None,
                            on_resize=requested.append)
    client = server.app.test_client()
    assert client.get("/api/layout-metrics").status_code == 403
    assert client.post("/api/window-size", json={"width": 500}).status_code == 403
    metrics = {"theme": "theme1", "regions": {"window": {"width": 360, "height": 220}}}
    server.publish_layout_metrics(metrics)
    assert client.get(f"/api/layout-metrics?token={server.token}").get_json() == metrics
    for value in (True, "500", -1, 9000, 10 ** 300):
        assert client.post(f"/api/window-size?token={server.token}", json={"width": value}).status_code == 400
    assert requested == []
    assert client.post(f"/api/window-size?token={server.token}", json={"width": 500, "height": 0}).status_code == 202
    assert requested == [{"width": 500, "height": 0}]
