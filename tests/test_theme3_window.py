"""Theme 3 resize events and translucent backing-store regressions."""

import os
import sys
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
    from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Bounds, Store
from stockwidget.intraday import Trend
from stockwidget.providers.base import Quote
from stockwidget.tdw import Point
from stockwidget.ui import window as window_module
from stockwidget.ui.window import TickerWindow
from stockwidget.webui.server import SettingsServer


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def card(app, tmp_path, monkeypatch):
    # Qt otherwise prints exceptions raised by event callbacks without failing
    # the test. Keep them observable through the whole resize/paint sequence.
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    store = Store(tmp_path / "config.json")
    config = store.update({"display_theme": "theme3", "symbols": ["603986"], "visible_rows": 1})
    window = TickerWindow(config)
    window.restore_bounds(Bounds(0, 0, 440, 400, manual_size=True), [])
    window.bounds_changed.connect(lambda bounds: store.update({"bounds": bounds}))
    quote = Quote.from_prices("603986", "兆易创新", 10.2, 10)
    trend = Trend(prices=[10, 10.2, 10.1, 10.2], minutes=["09:31:01", "09:31:05", "09:31:10", "09:31:15"],
                  trade_date="2026-11-06", tdw_points={
                      "2026-11-06 09:31:01": Point(.1, 10, ()),
                      "2026-11-06 09:31:05": Point(.6, 10.2, ("B1", "B2")),
                      "2026-11-06 09:31:10": Point(3.8, 10.1, ()),
                      "2026-11-06 09:31:15": Point(3.1, 10.2, ("S1", "S2")),
                  })
    window._sync_rows([quote], {quote.symbol: trend})
    window.show()
    app.processEvents()
    yield window, store, quote, trend
    window.close()
    app.processEvents()
    assert not errors


def send_grip(window, kind, position):
    release = kind == QEvent.MouseButtonRelease
    event = QMouseEvent(kind, QPointF(window.grip.mapFromGlobal(position)), QPointF(position),
                        Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                        Qt.NoButton if release else Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window.grip, event)


def test_card_grip_resizes_after_callback_and_scales_once_on_release(app, card, monkeypatch):
    window, store, quote, trend = card
    row = window._rows[quote.symbol]
    original_size = window.size()
    original_font = row.price_label.font().pixelSize()
    origin = window.grip.mapToGlobal(QPoint(5, 5))
    scales = []
    apply_scale = window._apply_scale

    def record_scale():
        scales.append(window._scale)
        apply_scale()

    monkeypatch.setattr(window, "_apply_scale", record_scale)
    quit_requests = []
    window.quit_requested.connect(lambda: quit_requests.append(True))
    send_grip(window, QEvent.MouseButtonPress, origin)
    app.processEvents()
    for delta in (QPoint(60, 40), QPoint(-80, -100), QPoint(80, 80)):
        before = window.size()
        send_grip(window, QEvent.MouseMove, origin + delta)
        assert window.size() == before  # no geometry changes inside mouseMoveEvent
        QTest.qWait(100)  # longer than the previous mid-drag font timer
        assert window.size() == original_size + QSize(delta.x(), delta.y())
        assert row.price_label.font().pixelSize() == original_font
        window._sync_rows([quote], {quote.symbol: trend})
        window.grab()  # quotes and paints continue during the drag
        assert not scales

    send_grip(window, QEvent.MouseButtonRelease, origin + delta)
    assert not scales  # release callback must also finish before relayout
    app.processEvents()
    assert scales == [pytest.approx(1.2)]
    assert row.price_label.font().pixelSize() == round(original_font * 1.2)
    assert window.size() == original_size + QSize(delta.x(), delta.y())
    assert window.isVisible() and not quit_requests
    window.flush_bounds()
    restored = Store(store.path).get()
    assert restored.bounds.scale == pytest.approx(1.2)
    assert (restored.bounds.width, restored.bounds.height) == (520, 480)


def test_card_background_from_settings_survives_resize_and_theme_switch(app, card):
    window, store, quote, _ = card
    server = SettingsServer(store, window.apply_config)
    client = server.app.test_client()

    for alpha in (0, .4, 1, 0):
        response = client.post(f"/api/config?token={server.token}", json={"background_alpha": alpha})
        assert response.status_code == 200
        window.resize_from_settings({"width": 460, "height": 420})
        app.processEvents()
        image = window.grab().toImage()
        row = window._rows[quote.symbol]
        for point in (window.scroll.mapTo(window, QPoint(2, 2)), row.mapTo(window, QPoint(1, 1))):
            assert image.pixelColor(point).alpha() == pytest.approx(255 * alpha * window._config.opacity, abs=2)

    for theme in ("theme1", "theme2", "theme3"):
        window.apply_config(store.update({"display_theme": theme}))
        app.processEvents()
    assert window._config.background_alpha == 0
    assert Store(store.path).get().background_alpha == 0
    assert window.grab().toImage().pixelColor(window.scroll.mapTo(window, QPoint(2, 2))).alpha() == 0
    row = window._rows[quote.symbol]
    image = window.grab().toImage()
    text_rect = row.price_label.geometry().translated(row.mapTo(window, QPoint()))
    assert max(image.pixelColor(x, y).alpha()
               for x in range(text_rect.left(), text_rect.right() + 1)
               for y in range(text_rect.top(), text_rect.bottom() + 1)) > 150


def test_background_paint_erases_old_pixels_including_rounded_corners(app, card, monkeypatch):
    window, _, _, _ = card
    # Model a native backing store retaining the old opaque frame. Painting
    # alpha=0 with SourceOver alone would leave every one of these pixels intact.
    image = QImage(window.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#11141c"))
    class BackingStorePainter(QPainter):
        def __init__(self, _):
            super().__init__(image)

    monkeypatch.setattr(window_module, "QPainter", BackingStorePainter)
    window._config = replace(window._config, background_alpha=0)
    window.paintEvent(None)
    assert image.pixelColor(30, 30).alpha() == 0
    assert image.pixelColor(0, 0).alpha() == 0


@pytest.mark.parametrize("size", [(220, 200), (440, 400)])
def test_real_card_window_renders_both_price_labels_and_short_dark_caption(app, card, monkeypatch, size):
    from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
    window, store, quote, trend = card
    window.resize(*size)
    window.apply_config(store.update({"show_high_low": True, "text_opacities": {"high_low": 1}}))
    window._sync_rows([quote], {quote.symbol: trend})
    window.update_depth(DepthSnapshot('603986', available=True, levels=(
        DepthLevel('bid', 10.0, 200), DepthLevel('ask', 10.2, 300))))
    app.processEvents()
    row = window._rows[quote.symbol]
    assert row.dark_label.text() == "暗"
    chart = row.sparkline
    captions = []
    original = QPainter.drawText

    def record(self, *args):
        if args[-1] in {"高 10.20", "低 10.00"}:
            captions.append((args[0], self.fontMetrics().boundingRect(args[-1]), self.font().bold()))
        return original(self, *args)

    monkeypatch.setattr(QPainter, "drawText", record)
    before = window.grab().toImage()
    assert len(captions) == 2
    assert not any(bold for _, _, bold in captions)
    origin = chart.mapTo(window, QPoint())
    glyph_rects = [rect.translated(round(point.x()) + origin.x(), round(point.y()) + origin.y())
                   for point, rect, _ in captions]
    window.apply_config(store.update({"text_opacities": {"high_low": 0}}))
    app.processEvents()
    after = window.grab().toImage()
    for rect in glyph_rects:
        assert window.rect().contains(rect)
        assert sum(before.pixel(x, y) != after.pixel(x, y)
                   for x in range(rect.left(), rect.right() + 1)
                   for y in range(rect.top(), rect.bottom() + 1)) > 10


@pytest.mark.parametrize('opacity', [.25, .7, 1.0])
def test_live_depth_repaints_composited_window_without_forced_child_grab(app, card, monkeypatch, opacity):
    import time
    from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
    from stockwidget.ui.sparkline import Sparkline
    window, store, quote, trend = card
    window.apply_config(store.update({'opacity': opacity, 'background_alpha': 0}))
    chart = window._rows[quote.symbol].sparkline
    chart.set_series([10, 10.1], ['10:00', '10:01'])
    chart.set_fill_width(True)
    levels = tuple(DepthLevel(side, base + index * step, 1000 if index == 500 else 10)
                   for side, base, step in [('bid', 10, -.001), ('ask', 10.01, .001)]
                   for index in range(1000))
    first = DepthSnapshot('603986', available=True, full_depth=True,
                          received_at=time.time(), levels=levels)
    second = replace(first, levels=tuple(replace(level, volume=500 if i == 700 else 2)
                                        if level.side == 'bid' else level for i, level in enumerate(levels)))
    paints, acknowledgments = [], []
    window.depth_painted.connect(lambda snapshot, displayed: acknowledgments.append((snapshot, displayed)))
    original = Sparkline._draw_depth
    def paint(widget, *args):
        if widget is chart:
            paints.append(widget._depth)
        return original(widget, *args)
    monkeypatch.setattr(Sparkline, '_draw_depth', paint)
    window.update_depth(first)
    QTest.qWait(80)
    first_image = window.grab().toImage()
    paints.clear()
    window.update_depth(second)
    QTest.qWait(80)
    assert paints and paints[-1] is second, 'screen refresh did not repaint the new depth'
    assert acknowledgments[-1] == (second, True)
    second_image = window.grab().toImage()
    assert first_image != second_image, 'opacity composite kept the old chart'
    paints.clear()
    window.update_depth(first)
    QTest.qWait(80)
    assert paints and paints[-1] is first
    # Clear the change marker before comparing the restored baseline image.
    chart.set_depth(None)
    window.update_depth(first)
    QTest.qWait(80)
    assert window.grab().toImage() == first_image, 'old long bars left ghost pixels after shrink'
