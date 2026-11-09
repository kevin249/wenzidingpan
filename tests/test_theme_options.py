"""Independent theme controls and exact-price drawing regressions."""
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(str(error), allow_module_level=True)

from stockwidget.config import Config, Store, sanitize
from stockwidget.mcp_depth import DEPTH_FULL, DepthLevel, DepthSnapshot
from stockwidget.providers.base import Quote
from stockwidget.ui import tdw_overlay
from stockwidget.ui.depth_ladder import DepthLadder
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.sparkline import Sparkline
from stockwidget.ui.theme import display_color
from stockwidget.ui.window import TickerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_theme_controls_are_independent_and_survive_restart(tmp_path):
    store = Store(tmp_path / "config.json")
    for i, theme in enumerate(("theme1", "theme2", "theme3")):
        store.update({"display_theme": theme, "chart_line_width": 1 + i,
                      "depth_line_width": .4 + i, "chart_depth_width": 180 + 40 * i,
                      "text_opacities": {"name": .1 * i, "tdw": .5}, "theme3_show_tdw": i != 2})
    store.update({"mcp_url": "http://127.0.0.1:8802/mcp"})
    store = Store(store.path)
    for i, theme in enumerate(("theme1", "theme2", "theme3")):
        config = store.update({"display_theme": theme})
        assert config.chart_line_width == 1 + i
        assert config.depth_line_width == pytest.approx(.4 + i)
        assert config.chart_depth_width == 180 + 40 * i
        assert config.text_opacities == {"name": pytest.approx(.1 * i), "tdw": .5}
        assert config.theme3_show_tdw is (i != 2)
        assert config.mcp_url == "http://127.0.0.1:8802/mcp"
    checked = sanitize({"chart_line_width": 999, "depth_line_width": -1,
                        "text_opacities": {"price": -1, "dark": 9, "name": float("nan"), "unknown": .5}})
    assert (checked.chart_line_width, checked.depth_line_width) == (6, .3)
    assert checked.text_opacities == {"price": 0, "dark": 1}


@pytest.mark.parametrize("height", [4, 8, 12, 20, 40, 80, 160])
@pytest.mark.parametrize("width", [8, 80, 400])
@pytest.mark.parametrize("gray", [False, True])
def test_four_tdw_guides_have_distinct_rows_at_every_panel_size(app, width, height, gray):
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    def ink(normal, alpha=None):
        return display_color(normal, grayscale=gray, level=150, alpha=alpha)
    with QPainter(image) as painter:
        font = QFont()
        font.setPixelSize(11)
        painter.setFont(font)
        guide_rows = tdw_overlay.draw_power_panel(painter, [], lambda _: 0, width - 1, width, height,
                                                  ink, QColor(Qt.transparent))
    rows = [y for y in range(height) if image.pixelColor(width // 2, y).alpha() >= 200]
    assert len(rows) == 4
    assert len(set(rows)) == 4
    assert rows == [guide_rows[tag] for tag in ("S2", "S1", "B2", "B1")]
    assert rows[0] < height / 2 < rows[-1]


def test_card_tdw_toggle_returns_space_to_price_and_guides_resize(app, monkeypatch):
    chart = Sparkline()
    chart.set_card_style(True)
    chart.set_series([10, 10.3, 10.1])
    chart.set_annotation_options(show_high_low=False, show_signals=True, show_open_line=False,
                                 show_fill=False, grayscale=False)
    calls = []
    original = tdw_overlay.draw_power_panel
    def panel(*args, **kwargs):
        calls.append(args[5])  # actual panel height after axes/footer reservations
        return original(*args, **kwargs)
    monkeypatch.setattr(tdw_overlay, "draw_power_panel", panel)
    for width, height in ((400, 300), (80, 16), (200, 100), (100, 8), (600, 400)):
        chart.resize(width, height)
        chart.grab()
        assert 4 <= calls[-1] <= height / 2
    assert len(calls) == 5 and len(set(calls)) >= 4
    chart.set_display_options(show_tdw=False)
    chart.grab()
    assert len(calls) == 5
    chart.set_display_options(show_tdw=True)
    chart.clear()
    chart.grab()  # guides remain while waiting for the first transaction
    assert len(calls) == 6
    chart.close()


@pytest.mark.parametrize("kind", ["spark", "ladder"])
def test_dense_book_keeps_every_price_and_its_individual_volume(app, monkeypatch, kind):
    levels = tuple(DepthLevel("bid", 9.99 + i * .0001, i + 1) for i in range(100))
    depth = DepthSnapshot("603986", available=True, depth_mode=DEPTH_FULL, levels=levels)
    image = QImage(400, 32, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    strokes = []
    draw = QPainter.drawLine
    def record(painter, *args):
        if painter.pen().widthF() == pytest.approx(.6) and len(args) == 2:
            strokes.append((args[0].x(), args[1].x(), args[0].y()))
        return draw(painter, *args)
    monkeypatch.setattr(QPainter, "drawLine", record)
    if kind == "spark":
        widget = Sparkline()
        widget.set_card_style(True)
        widget.set_display_options(depth_width=240, depth_line_width=.6)
        widget.set_depth(depth)
        span = widget._book_width(400)
        assert span == pytest.approx(399 * .5)
        with QPainter(image) as painter:
            widget._draw_depth(painter, 400, 32, lambda price: (10.01 - price) * 500, span)
        assert len(strokes) == len(levels)
        assert len({y for _, _, y in strokes}) == len(levels)
        assert all(0 <= y <= 32 for _, _, y in strokes)
    else:
        widget = DepthLadder()
        widget.resize(400, 32)
        widget.apply_config(Config(display_theme="theme2", depth_line_width=.6))
        widget.set_depth(depth)
        layout = widget._layout()
        span = abs(layout.depth_edge - layout.axis_x)
        with QPainter(image) as painter:
            widget._draw_depth(painter, layout)
    assert len(strokes) == len(levels)
    assert len({y for _, _, y in strokes}) == len(levels)
    assert [abs(x1 - x0) for x0, x1, _ in strokes] == pytest.approx([
        (span * row.volume / 100 if kind == "spark" else max(1, span * row.volume / 100))
        for row in reversed(levels)])
    widget.close()


@pytest.mark.parametrize("theme", ["theme1", "theme3"])
@pytest.mark.parametrize("gray", [False, True])
def test_each_header_region_has_its_own_alpha(app, theme, gray):
    config = sanitize({"display_theme": theme, "opacity": 1, "background_alpha": 0,
                       "grayscale": gray, "show_sparkline": False,
                       "text_opacities": {"name": 0, "price": .25, "percent": .75, "dark": .5}})
    window = TickerWindow(config)
    quote = Quote.from_prices("603986", "兆易创新", 10.25, 10)
    quote.dark_fund = 100000000
    window._sync_rows([quote])
    window.resize(700, 150)
    window.show()
    app.processEvents()
    image = window.grab().toImage()
    row = window._rows[quote.symbol]
    for widget, alpha in ((row.name_label, 0), (row.price_label, .25), (row.percent_label, .75), (row.dark_value, .5)):
        origin = widget.mapTo(window, QPoint())
        maximum = max(image.pixelColor(x, y).alpha()
                      for x in range(origin.x(), origin.x() + widget.width())
                      for y in range(origin.y(), origin.y() + widget.height()))
        assert maximum == pytest.approx(255 * alpha, abs=3)
    window.close()


def test_plot_line_width_and_chart_text_alpha_reach_the_painter(app, monkeypatch):
    chart = Sparkline()
    chart.resize(400, 280)
    chart.set_card_style(True)
    chart.set_series([10., 11., 10.5], ["09:31", "09:32", "09:33"])
    chart.set_display_options(chart_line_width=3.2, text_opacities={"chart": .2, "high_low": .4, "tdw": .6})
    texts, paths = [], []
    text, path = QPainter.drawText, QPainter.drawPath
    def record_text(p, *args):
        texts.append((str(args[-1]), p.pen().color().alpha()))
        return text(p, *args)
    def record_path(p, *args):
        paths.append(p.pen().widthF())
        return path(p, *args)
    monkeypatch.setattr(QPainter, "drawText", record_text)
    monkeypatch.setattr(QPainter, "drawPath", record_path)
    chart.grab()
    assert 3.2 in paths
    assert not any(caption == "09:30" for caption, _ in texts)
    assert any(alpha == 51 for caption, alpha in texts)
    assert ("11.00", 102) in texts
    assert any(caption.startswith("TDW") and alpha == 153 for caption, alpha in texts)
    chart.close()


def test_theme2_quote_alpha_is_applied_once_including_grayscale(app, monkeypatch):
    widget = DepthLadder()
    widget.resize(400, 200)
    widget.apply_config(Config(display_theme="theme2", grayscale=True,
                               text_opacities={"price": .2, "percent": .7}))
    widget.set_quote(10.25, 2.5, QColor("red"))
    texts = []
    draw = QPainter.drawText
    def record(p, *args):
        texts.append((str(args[-1]), p.pen().color().alpha()))
        return draw(p, *args)
    monkeypatch.setattr(QPainter, "drawText", record)
    widget.grab()
    assert ("10.25", 51) in texts
    assert any("2.50" in caption and alpha == pytest.approx(255 * .7, abs=1) for caption, alpha in texts)
    widget.close()
