"""数字尺寸、实际尺寸反馈与原有窗口缩放相互配合。"""

import os
from dataclasses import replace

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


@pytest.mark.parametrize("row_style", ["sides", "stacked", "top_bottom"])
@pytest.mark.parametrize("changed", [
    {"component_sizes": {"row": {"height": 180}, "name": {"width": 130}}},
    {"chart_height": 100},
    {"stock_price_font_size": 24},
])
def test_width_only_setting_after_layout_changes_preserves_fonts(app, row_style, changed):
    window = TickerWindow(Config(row_style=row_style))
    quote = Quote.from_prices("603986", "兆易创新", 286, 281)
    window._sync_rows([quote])
    window.show()
    app.processEvents()
    try:
        window._manual_size = True
        window._scale = 1.35
        window.apply_config(replace(window._config, **changed))
        app.processEvents()
        row = window._rows[quote.symbol]
        fonts = [widget.font().pixelSize() for widget in
                 (row.name_label, row.price_label, row.percent_label, row.dark_value)]
        height = window.height()
        window.resize_from_settings({"width": min(window.width() + 30, 700)})
        app.processEvents()
        assert window.height() == height
        assert window._scale == pytest.approx(1.35)
        assert [widget.font().pixelSize() for widget in
                (row.name_label, row.price_label, row.percent_label, row.dark_value)] == fonts
    finally:
        window.close()


@pytest.mark.parametrize("theme", ["theme1", "theme2"])
def test_numeric_height_round_trips_restore_fonts_and_size(app, theme):
    window = TickerWindow(Config(display_theme=theme, row_style="top_bottom"))
    quote = Quote.from_prices("603986", "兆易创新", 286, 281)
    window._sync_rows([quote])
    window.show()
    app.processEvents()
    try:
        window._manual_size = True
        window.resize(450, 200)  # Deliberately different from the natural content height.
        app.processEvents()
        row = window._rows[quote.symbol]
        original = window.size()
        fonts = (row.price_label.font().pixelSize(), row.theme2_depth._config.stock_price_font_size)
        for _ in range(3):
            window.resize_from_settings({"height": 280})
            window.resize_from_settings({"height": original.height()})
            app.processEvents()
            assert window.size() == original
            assert window._scale == pytest.approx(1.0)
            assert (row.price_label.font().pixelSize(),
                    row.theme2_depth._config.stock_price_font_size) == fonts
    finally:
        window.close()


def test_saved_small_scale_survives_reload_with_tall_explicit_card(app, tmp_path):
    store = Store(tmp_path / "config.json")
    store.update({"component_sizes": {"row": {"height": 260}}, "bounds": {
        "x": 0, "y": 0, "width": 450, "height": 210, "scale": 0.7, "manual_size": True,
    }})
    config = Store(store.path).get()
    window = TickerWindow(config)
    try:
        window.restore_bounds(config.bounds, [app.primaryScreen().availableGeometry()])
        window._sync_rows([Quote.from_prices("603986", "兆易创新", 286, 281)])
        app.processEvents()
        assert window._scale == pytest.approx(0.7)
        assert window._rows["603986"].price_label.font().pixelSize() == round(config.stock_price_font_size * 0.7)
    finally:
        window.close()


def test_reset_scale_recovers_fonts_without_losing_component_settings(app):
    config = Config(row_style="top_bottom", chart_height=80,
                    component_sizes={"name": {"width": 130}}, stock_price_font_size=18)
    window = TickerWindow(config)
    window._sync_rows([Quote.from_prices("603986", "兆易创新", 286, 281)])
    window.show()
    app.processEvents()
    try:
        window._manual_size = True
        window._scale = 3.0
        window._apply_scale()
        window.resize(500, 300)
        app.processEvents()
        bounds = []
        window.bounds_changed.connect(bounds.append)
        window.resize_from_settings({"reset_scale": True})
        app.processEvents()
        assert window.size() == QSize(500, 300)
        assert window._scale == 1.0
        assert bounds[-1]["scale"] == 1.0
        row = window._rows["603986"]
        assert row.price_label.font().pixelSize() == 18
        assert row.name_label.maximumWidth() == 130
        assert row.sparkline.maximumHeight() == 80
        assert window._config is config
    finally:
        window.close()


def test_reset_scale_api_requires_token_and_boolean(tmp_path):
    requested = []
    server = SettingsServer(Store(tmp_path / "config.json"), lambda config: None,
                            on_resize=requested.append)
    client = server.app.test_client()
    assert client.post("/api/window-size", json={"reset_scale": True}).status_code == 403
    url = f"/api/window-size?token={server.token}"
    for value in (1, "true", None, []):
        assert client.post(url, json={"reset_scale": value}).status_code == 400
    assert requested == []
    assert client.post(url, json={"reset_scale": True}).status_code == 202
    assert requested == [{"width": 0, "height": 0, "reset_scale": True}]
