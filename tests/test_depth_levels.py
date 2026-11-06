"""Each received price level must reach the painter without binning or clipping."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from stockwidget.config import Config
from stockwidget.mcp_depth import parse_depth_payload
from stockwidget.ui.depth_ladder import DepthLadder
from stockwidget.ui.sparkline import Sparkline


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def book():
    raw = [{"side": side, "price": base + index / 100, "volume": 1000 + offset + index}
           for side, base, offset in (("bid", 10, 0), ("ask", 200, 1000))
           for index in range(1000)]
    snapshot = parse_depth_payload("600000", {"symbol": "600000", "available": True, "levels": raw})
    assert len(snapshot.levels) == len(raw)
    return snapshot


@pytest.mark.parametrize("theme", ["theme1", "theme3", "left", "right"])
@pytest.mark.parametrize("height", [32, 320])
def test_every_level_is_drawn_with_its_original_volume(app, monkeypatch, book, theme, height):
    image = QImage(600, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    strokes = []
    original = QPainter.drawLine

    def record(self, *args):
        pen = self.pen()
        if len(args) == 2 and pen.style() == Qt.SolidLine and pen.capStyle() == Qt.FlatCap:
            start, end = args
            strokes.append((start.y(), abs(end.x() - start.x())))
        return original(self, *args)

    monkeypatch.setattr(QPainter, "drawLine", record)
    try:
        if theme in {"left", "right"}:
            widget = DepthLadder()
            widget.apply_config(Config(display_theme="theme2", theme2_side=theme))
            widget.setFixedSize(600, height)
            # Previously this quote removed all asks from the book.
            widget.set_quote(999, None, QColor("white"))
            widget.set_depth(book)
            layout = widget._layout()
            span = abs(layout.depth_edge - layout.axis_x)
            widget._draw_depth(painter, layout)
        else:
            widget = Sparkline()
            widget.set_card_style(theme == "theme3")
            widget.resize(600, height)
            widget.set_depth(book)
            span = 200
            # Every book price lies outside this hypothetical candle window.
            widget._draw_depth(painter, 600, height, lambda price: 10000 + price, span)
    finally:
        painter.end()
    assert len(strokes) == len(book.levels)
    assert len({y for y, _ in strokes}) == len(book.levels)
    assert all(0 <= y < height for y, _ in strokes)
    expected = sorted(span * level.volume / 2999 for level in book.levels)
    assert sorted(length for _, length in strokes) == pytest.approx(expected)
    widget.close()


@pytest.mark.parametrize("card", [False, True])
def test_depth_renders_before_candles_arrive(app, monkeypatch, book, card):
    widget = Sparkline()
    widget.resize(500, 240)
    widget.set_card_style(card)
    widget.set_depth(book)
    calls = []
    original = widget._draw_depth

    def record(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(widget, "_draw_depth", record)
    assert not widget.points
    widget.grab()
    assert calls == [True]
