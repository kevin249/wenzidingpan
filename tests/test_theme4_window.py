"""侧栏的盘口价轴、镜像、单股展开和主题隔离。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stockwidget import tdw
from stockwidget.config import THEME4_MIN_WIDTH, Store
from stockwidget.intraday import Trend
from stockwidget.kline import Bar
from stockwidget.mcp_depth import DEPTH_FULL, DepthLevel, DepthSnapshot
from stockwidget.poller import Snapshot
from stockwidget.providers.base import Quote
from stockwidget.ui.docked_book import price_ticks, reference_prices, volume_gray
from stockwidget.ui import tdw_overlay
from stockwidget.ui.window import TickerWindow


def test_docked_watchlist_and_theme_roundtrip(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "settings.json")
    symbols = ["603986", "300223", "600000"]
    old = store.update({"display_theme": "theme3", "symbols": symbols,
                        "stock_price_font_size": 32, "theme3_price_layout": "split_columns"})
    window = TickerWindow(old)
    quotes = [Quote(symbol, symbol, 100 + i) for i, symbol in enumerate(symbols)]
    bars = [Bar("2026-11-20 09:30:00", 100, 100, 100, 100),
            Bar("2026-11-20 10:30:00", 101, 101, 101, 101),
            Bar("2026-11-20 13:00:00", 102, 102, 102, 102),
            Bar("2026-11-20 15:00:00", 103, 103, 103, 103)]
    trend = Trend(realtime_bars=bars, average_prices=[("09:30", 100), ("15:00", 101.5)],
                  tdw_points={b.time: tdw.Point(i, b.close, ()) for i, b in enumerate(bars)})
    snap = Snapshot("mock", quotes, trends={s: trend for s in symbols})
    window.update_snapshot(snap, "")
    cfg = store.update({"display_theme": "theme4", "tdw_recent_five": True, "grayscale": False})
    window.apply_config(cfg)
    window.show()
    try:
        app.processEvents()
        panel = window.docked_book
        assert panel.height() == panel.preferred_height() == 376
        assert window.height() < window.screen().availableGeometry().height()
        assert panel.isVisible() and not window.focus_host.isVisible()
        assert panel.visible_symbols() == symbols[:2] and not panel.expanded
        assert cfg.background_alpha == 0 and cfg.theme4_side == "left"
        assert window.x() == window.screen().geometry().left()

        depth = DepthSnapshot(symbols[0], (DepthLevel("ask", 101, 800), DepthLevel("bid", 99, 20)),
                              available=True, depth_mode=DEPTH_FULL)
        window.update_depth(depth)
        panel.grab()
        assert panel._receipts[symbols[0]] == (depth, True)
        QTest.mouseClick(panel, Qt.LeftButton, pos=QPoint(55, panel.height() * 3 // 4))
        assert panel.expanded == {symbols[1]}
        draw, paths, power_draws, candles, axis_labels = [], [], [], [], []
        guide_alphas = []
        original = panel._draw_chart
        stroke_path = QPainter.strokePath
        draw_line = QPainter.drawLine
        draw_panel = tdw_overlay.draw_power_panel
        draw_text = panel._draw_text

        def record(p, trend, *args):
            draw.extend(trend.realtime_bars)
            original(p, trend, *args)

        def record_path(painter, path, pen):
            paths.append((path, pen.color(), pen.widthF()))
            return stroke_path(painter, path, pen)

        def record_line(painter, *args):
            pen = painter.pen()
            if pen.style() == Qt.DashLine:
                guide_alphas.append(pen.color().alphaF())
            return draw_line(painter, *args)

        def record_tdw(p, power, x, *args, **kwargs):
            power_draws.append((power, [x(i) for i in range(len(power))], p.transform().dy(), kwargs["show_caption"]))
            return draw_panel(p, power, x, *args, **kwargs)

        def record_text(p, text, rect, *args, **kwargs):
            if p.transform().dy() > panel.height() / 2:
                axis_labels.append((text, rect.x(), rect.center().y(), p.transform().dy()))
            return draw_text(p, text, rect, *args, **kwargs)

        monkeypatch.setattr(panel, "_draw_chart", record)
        monkeypatch.setattr(QPainter, "strokePath", record_path)
        monkeypatch.setattr(QPainter, "drawLine", record_line)
        monkeypatch.setattr(tdw_overlay, "draw_power_panel", record_tdw)
        monkeypatch.setattr(panel, "_draw_text", record_text)
        with monkeypatch.context() as chart_paint:
            chart_paint.setattr(tdw_overlay, "draw_realtime_candles", lambda *args, **kwargs: candles.append(args))
            panel.grab()
        assert not candles
        assert draw == bars
        [(trace, _color, chart_width)] = [entry for entry in paths if entry[0].elementCount() == 4]
        assert chart_width == cfg.chart_line_width
        first, second, afternoon, last = (trace.elementAt(i) for i in range(4))
        assert first.y > second.y > afternoon.y > last.y
        assert (afternoon.x - first.x) / (last.x - first.x) == pytest.approx(.5)
        assert (second.x - first.x) / (last.x - first.x) == pytest.approx(.25)
        assert [text for text, *_ in axis_labels] == ["100.00", "101.00", "102.00", "103.00"]
        assert all(x > last.x and top == panel.height() / 2 + 42 for _, x, _, top in axis_labels)
        assert [y for _, _, y, _ in axis_labels] == pytest.approx([point.y for point in (first, second, afternoon, last)])
        [(average, _color, average_width)] = [entry for entry in paths if entry[1] == QColor("#ffff00")]
        assert average_width == cfg.average_line_width
        assert (average.elementAt(0).x, average.elementAt(1).x) == (first.x, last.x)
        power, power_x, power_top, show_caption = power_draws[-1]
        assert power == [0, 1, 2, 3] and not show_caption
        assert power_x == [point.x for point in (first, second, afternoon, last)]
        price_top = axis_labels[0][3]
        assert power_top - price_top - first.y == pytest.approx(max(chart_width, average_width) / 2)
        assert guide_alphas == [1] * 6
        for opacity in (.35, 0):
            window.apply_config(store.update({"chart_line_width": 2.6, "average_line_width": 3.8,
                                              "theme4_depth_opacity": opacity}))
            paths.clear()
            guide_alphas.clear()
            panel.grab()
            [(trace_ink, trace_width)] = [(color, width) for path, color, width in paths if path.elementCount() == 4]
            [(avg_ink, avg_width)] = [(color, width) for _, color, width in paths if color.name() == "#ffff00"]
            assert (trace_width, avg_width) == (2.6, 3.8)
            assert [trace_ink.alphaF(), avg_ink.alphaF()] == pytest.approx([opacity] * 2, abs=1 / 255)
            assert guide_alphas == pytest.approx([opacity] * 6, abs=1 / 255)
        QTest.mouseClick(panel, Qt.LeftButton, pos=QPoint(55, panel.height() * 3 // 4))
        assert not panel.expanded
        draw.clear()
        panel.grab()
        assert not draw
        QTest.mouseClick(panel, Qt.LeftButton, pos=QPoint(55, panel.height() * 3 // 4))
        draw.clear()
        panel.set_snapshot(Snapshot("mock", quotes, trends={
            symbols[1]: Trend(realtime_bars=[Bar("2026-11-20 10:30", 99, 101, 99, 101)]),
        }))
        panel.grab()
        assert not draw
        window.apply_config(store.update({"theme4_side": "right", "theme4_width": 340,
                                          "theme4_tone": "dark"}))
        assert window.width() == 340 and panel.expanded == {symbols[1]}
        assert window.geometry().right() == window.screen().geometry().right()
        app.sendEvent(panel, QWheelEvent(QPointF(30, 30), QPointF(30, 30), QPoint(),
                      QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False))
        assert panel.visible_symbols() == symbols[1:]
        restored = store.update({"display_theme": "theme3"})
        window.apply_config(restored)
        assert window.focus_host.isVisible() and not panel.isVisible()
        assert list(window._rows) == symbols
        assert restored.stock_price_font_size == 32 and restored.theme3_price_layout == "split_columns"
        assert Store(store.path).update({"display_theme": "theme4"}).theme4_width == 340
    finally:
        window.close()
        app.processEvents()


def test_price_axis_ticks_support_flat_and_subunit_prices():
    assert price_ticks(22.62, 22.62, 4) == (22.62,)
    assert price_ticks(.2536, .2542, 4) == pytest.approx((.2536, .2538, .2540, .2542))


def test_two_percent_book_uses_only_in_band_levels(tmp_path, monkeypatch):
    from stockwidget.ui.docked_book import DockedBook
    app = QApplication.instance() or QApplication([])
    panel = DockedBook()
    config = Store(tmp_path / "config.json").update({"display_theme": "theme4", "symbols": ["600000"]})
    depths = {}
    panel.configure(config, depths)
    panel.resize(360, panel.preferred_height())
    panel.set_snapshot(Snapshot("mock", [Quote("600000", "示例", 100.0)]))
    levels = tuple(DepthLevel("bid" if n < 100 else "ask", n, v)
                   for n, v in [(97.99, 9000), (98., 30), (100.01, 150), (102., 50), (102.01, 8000)])
    depths["600000"] = DepthSnapshot("600000", levels, available=True, depth_mode=DEPTH_FULL)
    seen = []

    def record(painter, visible, y, *args):
        seen.append(([level.volume for level in visible], [y(level.price) for level in visible]))

    monkeypatch.setattr(panel, "_draw_book", record)
    panel.grab()
    volumes, positions = seen[-1]
    assert volumes == [30, 150, 50]
    assert positions[0] == panel.height() - 42 and positions[-1] == 42
    panel.set_snapshot(Snapshot("mock", [Quote("600000", "示例", 110.0)]))
    seen.clear()
    panel.grab()
    assert not seen and panel._receipts["600000"][1] is False
    panel.close()


def test_collapsed_book_keeps_chart_side_empty(tmp_path, monkeypatch):
    from stockwidget.ui.docked_book import DockedBook
    app = QApplication.instance() or QApplication([])
    panel = DockedBook()
    config = Store(tmp_path / "config.json").update({"display_theme": "theme4", "symbols": ["600000"]})
    panel.configure(config, {})
    panel.resize(360, panel.preferred_height())
    panel.set_snapshot(Snapshot("mock", [Quote("600000", "示例", 100.0)]))
    texts, lines = [], []
    draw_text = QPainter.drawText
    draw_line = QPainter.drawLine

    def record_text(painter, *args):
        texts.append((args[-1], painter.worldTransform().map(args[0]).x()))
        return draw_text(painter, *args)

    def record_line(painter, start, end):
        lines.append((start, end, painter.pen().style()))
        return draw_line(painter, start, end)

    monkeypatch.setattr(QPainter, "drawText", record_text)
    monkeypatch.setattr(QPainter, "drawLine", record_line)
    panel.grab()
    assert [text for text, _ in texts] == ["示例", "—", "—", "101.00", "100.00", "99.00"]
    assert len(lines) == 3
    assert all(0 < a.y() < panel.height() and b.x() < panel.width() / 2 for a, b, _ in lines)
    assert all(style == Qt.DashLine for _, _, style in lines)
    assert all(0 < x - line[1].x() <= 3.1 for (_, x), line in zip(texts[-3:], lines))
    panel.close()


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("width", [THEME4_MIN_WIDTH, 120, 163, 290, 560])
def test_chart_click_preserves_book_layout(tmp_path, monkeypatch, side, width):
    from stockwidget.ui.docked_book import DockedBook
    app = QApplication.instance() or QApplication([])
    panel = DockedBook()
    config = Store(tmp_path / "config.json").update({
        "display_theme": "theme4", "theme4_side": side, "symbols": ["600000"],
        "stock_price_font_size": 12, "chart_label_font_size": 12,
    })
    levels = (DepthLevel("bid", 98, 100), DepthLevel("ask", 102, 100))
    panel.configure(config, {"600000": DepthSnapshot("600000", levels, available=True)})
    panel.resize(width, panel.preferred_height())
    panel.set_snapshot(Snapshot("mock", [Quote("600000", "示例", 100)], trends={
        "600000": Trend(realtime_bars=[
            Bar("2026-11-20 09:30:00", 100, 100, 100, 100),
            Bar("2026-11-20 15:00:00", 101, 101, 101, 101),
        ]),
    }))
    panel.show()
    app.processEvents()
    book, chart = [], []
    book_strokes, curve_spans = [], []
    draw_book, draw_chart = panel._draw_book, panel._draw_chart
    draw_lines, stroke_path = QPainter.drawLines, QPainter.strokePath

    def record_book(p, levels, y, edge, extent, dark):
        book.append((y(102), y(98), edge, extent))
        draw_book(p, levels, y, edge, extent, dark)

    def record_chart(p, trend, rect, padding, axis_width, ink):
        chart.append((rect, axis_width))
        draw_chart(p, trend, rect, padding, axis_width, ink)

    def record_lines(p, lines):
        book_strokes.extend(line.length() for line in lines)
        return draw_lines(p, lines)

    def record_path(p, path, pen):
        if path.elementCount() == 2:
            curve_spans.append(path.elementAt(1).x - path.elementAt(0).x)
        return stroke_path(p, path, pen)

    monkeypatch.setattr(panel, "_draw_book", record_book)
    monkeypatch.setattr(panel, "_draw_chart", record_chart)
    monkeypatch.setattr(QPainter, "drawLines", record_lines)
    monkeypatch.setattr(QPainter, "strokePath", record_path)
    try:
        collapsed = panel.grab().toImage()
        assert not chart
        QTest.mouseClick(panel, Qt.LeftButton, pos=panel.rect().center())
        expanded = panel.grab().toImage()
        [(top, bottom, edge, extent), expanded_book] = book
        [(rect, axis_width)] = chart
        assert expanded_book == (top, bottom, edge, extent)
        assert (rect.top(), rect.bottom()) == pytest.approx((top, bottom))
        assert abs(extent) > 0
        assert max(book_strokes) == pytest.approx(abs(extent))
        assert curve_spans == pytest.approx([2 * max(book_strokes)])
        if width >= 290:
            assert axis_width > 0
        chart_box = rect.toAlignedRect()
        book_box = panel.rect()
        if side == "left":
            assert edge == 4 and edge < edge + extent < rect.left()
            assert rect.right() == pytest.approx(width - 4)
            book_box.setRight(chart_box.left() - 1)
        else:
            assert edge == width - 4 and rect.right() < edge + extent < edge
            assert rect.left() == 4
            book_box.setLeft(chart_box.right() + 1)
        assert expanded.copy(book_box) == collapsed.copy(book_box)
        assert expanded.copy(chart_box) != collapsed.copy(chart_box)
        QTest.mouseClick(panel, Qt.LeftButton, pos=panel.rect().center())
        assert panel.grab().toImage() == collapsed
    finally:
        panel.close()


def test_narrow_header_and_quotes_use_configured_styles(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "config.json")
    conf = store.update({
        "display_theme": "theme4", "theme4_width": 120, "symbols": ["603533"],
        "stock_price_font_size": 12, "chart_label_font_size": 12,
        "stock_name_font_size": 12, "dark_trade_font_size": 12, "stock_price_bold": True, "dark_trade_bold": True,
        "grayscale": False, "stock_price_color": "#2bb866", "text_opacities": {"price": .5, "chart": 1},
        "theme4_name_italic": True, "theme4_name_shadow": True,
        "theme4_price_italic": True, "theme4_price_shadow": True,
        "theme4_dark_italic": True, "theme4_dark_shadow": True,
    })
    window = TickerWindow(conf)
    window.show()
    try:
        app.processEvents()
        panel = window.docked_book
        assert window.width() == 120
        quote = Quote("603533", "掌阅科技", 205.88, change_percent=-1.72, dark_fund=-198_000_000)
        panel.set_snapshot(Snapshot("mock", [quote]))
        window.update_depth(DepthSnapshot("603533", (DepthLevel("bid", 205, 50),),
                                          available=True, source_at="2026-11-20T07:28:35Z",
                                          fetched_at="2026-11-20T17:13:56+08:00"))
        texts, shadows, strokes = [], [], []
        appearances = {}
        draw_text, fill_path = QPainter.drawText, QPainter.fillPath

        def record_text(painter, origin, text):
            font = painter.font()
            texts.append((text, origin.x(), font.pixelSize(), font.bold(), font.italic()))
            appearances[text] = (painter.pen().color(), painter.worldTransform().m11())
            return draw_text(painter, origin, text)

        def record_shadow(painter, path, color):
            shadows.append(color)
            return fill_path(painter, path, color)

        monkeypatch.setattr(QPainter, "drawText", record_text)
        monkeypatch.setattr(QPainter, "fillPath", record_shadow)
        with monkeypatch.context() as text_paint:
            text_paint.setattr(QPainter, "strokePath", lambda *args: strokes.append(args))
            panel.grab()
        assert not strokes
        assert [text[0] for text in texts] == ["掌阅科技", "15:28:35", "-1.98亿", "-1.72%",
                                                "207.94", "205.88", "203.82"]
        assert texts[0][1] < texts[1][1] < texts[2][1] < texts[3][1]
        assert texts[0][-1] and texts[2][-2:] == (True, True)
        assert all(text[2:] == texts[1][2:] for text in texts[4:])
        assert texts[1][-2:] == (True, True)
        assert appearances["15:28:35"] == appearances["205.88"]
        assert appearances["15:28:35"][0].name() == "#2bb866"
        assert appearances["15:28:35"][0].alpha() == 128
        assert len(shadows) == 6

        window.apply_config(store.update({"theme4_width": 420, "dark_trade_font_size": 21}))
        app.processEvents()
        texts.clear()
        panel.grab()
        sizes = {text: size for text, _, size, _, _ in texts}
        assert sizes["-1.98亿"] == 21
        assert sizes["15:28:35"] == sizes["205.88"] == 12
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("side,direction", [("left", 1), ("right", -1)])
@pytest.mark.parametrize("width,change,dy", [(620, 90, 60), (150, -70, -90), (100, -90, -500)])
def test_inner_bottom_corner_resizes_and_saves_while_docked(tmp_path, side, direction, width, change, dy):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "config.json")
    cfg = store.update({"display_theme": "theme4", "theme4_side": side,
                        "theme4_width": width, "symbols": ["600000"]})
    window = TickerWindow(cfg)
    window.dock_settings_requested.connect(lambda patch: window.apply_config(store.update(patch)))
    window.show()
    try:
        app.processEvents()
        panel = window.docked_book
        anchor = (lambda: window.geometry().left()) if side == "left" else (lambda: window.geometry().right())
        edge = anchor()
        original = window.size()
        corner = QPoint(panel.width() - 4 if side == "left" else 4, panel.height() - 4)
        start = panel.mapToGlobal(corner)
        target = start + QPoint(direction * change, dy)
        QTest.mousePress(panel, Qt.LeftButton, pos=corner)
        for event_type, button, buttons in [(QEvent.MouseMove, Qt.NoButton, Qt.LeftButton),
                                             (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton)]:
            event = QMouseEvent(event_type, QPointF(panel.mapFromGlobal(target)), QPointF(target),
                                button, buttons, Qt.NoModifier)
            app.sendEvent(panel, event)
            assert window.width() == max(THEME4_MIN_WIDTH, original.width() + change)
            assert window.height() == max(panel.minimum_height() + window._chrome_height(), original.height() + dy)
            assert anchor() == edge
        saved = Store(store.path).get()
        assert saved.theme4_width == window.width() and saved.theme4_height == window.height()
        assert not panel.expanded
        window.apply_config(store.update({"theme4_height": 0}))
        assert window.height() == original.height()
    finally:
        window.close()
        app.processEvents()


def test_free_resize_uses_bottom_right_even_with_mirrored_book(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "config.json")
    cfg = store.update({"display_theme": "theme4", "theme4_side": "right", "theme4_snap": False,
                        "bounds": {"x": 90, "y": 80, "width": 140, "height": 220},
                        "theme4_width": 140, "theme4_height": 220, "symbols": ["600000", "603533"]})
    window = TickerWindow(cfg)
    window.restore_bounds(cfg.bounds, [window.screen().availableGeometry()])
    window.dock_settings_requested.connect(lambda patch: window.apply_config(store.update(patch)))
    window.show()
    try:
        app.processEvents()
        panel = window.docked_book
        origin = window.pos()
        start = QPoint(panel.width() - 4, panel.height() - 4)
        target = panel.mapToGlobal(start) + QPoint(30, -95)
        QTest.mousePress(panel, Qt.LeftButton, pos=start)
        app.sendEvent(panel, QMouseEvent(QEvent.MouseButtonRelease, QPointF(panel.mapFromGlobal(target)),
                                        QPointF(target), Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        assert window.pos() == origin
        assert (window.width(), window.height()) == (170, 125)
        assert (Store(store.path).get().theme4_width, Store(store.path).get().theme4_height) == (170, 125)
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("target_widget", ["docked_book", "title_bar", "handle"])
@pytest.mark.parametrize("snap", [True, False])
def test_drag_follows_pointer_then_persists_position(tmp_path, target_widget, snap):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "config.json")
    cfg = store.update({"display_theme": "theme4", "symbols": ["600000"],
                        "theme4_snap": snap,
                        "show_title_buttons": target_widget == "title_bar",
                        "click_through": target_widget == "handle"})
    window = TickerWindow(cfg)
    window.dock_settings_requested.connect(lambda patch: window.apply_config(store.update(patch)))
    window.show()
    try:
        app.processEvents()
        target = getattr(window, target_widget)
        local = QPoint(10, 10)
        room = window.screen().availableGeometry()
        for x, dy, side in [(room.left() + 90, 75, "left"), (room.center().x() + 22, 90, "right")]:
            start = target.mapToGlobal(local)
            start_y = window.y()
            end = QPoint(x, start.y() + dy)
            free_x = x - (start.x() - window.x())
            QTest.mousePress(target, Qt.LeftButton, pos=local)
            for kind, button, held in [(QEvent.MouseMove, Qt.NoButton, Qt.LeftButton),
                                       (QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton)]:
                event = QMouseEvent(kind, QPointF(target.mapFromGlobal(end)), QPointF(end),
                                    button, held, Qt.NoModifier)
                app.sendEvent(target, event)
                assert window.y() == start_y + dy
                if kind == QEvent.MouseMove:
                    assert target.mapToGlobal(local) == end
            if snap:
                assert window.x() == (room.left() if side == "left" else room.right() - window.width() + 1)
            else:
                assert window.x() == free_x
            saved = Store(store.path).get()
            assert saved.theme4_snap is snap
            assert saved.theme4_side == (side if snap else "left")
            assert (saved.bounds.x, saved.bounds.y) == (window.x(), window.y())
            assert not window.docked_book.expanded
        restored = TickerWindow(saved)
        restored.restore_bounds(saved.bounds, [room])
        restored.show()
        try:
            app.processEvents()
            assert restored.geometry() == window.geometry()
        finally:
            restored.close()
        if not snap:
            before = window.pos()
            window.apply_config(store.update({"theme4_snap": True, "theme4_side": "right"}))
            assert window.geometry().right() == room.right() and window.y() == before.y()
            before = window.pos()
            window.apply_config(store.update({"theme4_snap": False}))
            assert window.pos() == before
    finally:
        window.close()
        app.processEvents()


def test_white_background_book_still_has_shaded_volume(tmp_path):
    from stockwidget.ui.docked_book import DockedBook
    app = QApplication.instance() or QApplication([])
    panel = DockedBook()
    panel.setAttribute(Qt.WA_TranslucentBackground)
    config = Store(tmp_path / "config.json").update({"display_theme": "theme4", "symbols": ["600000"]})
    levels = (DepthLevel("bid", 98.50, 100), DepthLevel("ask", 101.50, 50))
    depth = DepthSnapshot("600000", levels, available=True, depth_mode=DEPTH_FULL)
    panel.configure(config, {"600000": depth})
    panel.resize(360, panel.preferred_height())
    panel.set_snapshot(Snapshot("mock", [Quote("600000", "示例", 100.0)]))
    layer = panel.grab()
    canvas = QImage(panel.size(), QImage.Format_RGB32)
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, layer)
    painter.end()
    dark = min(canvas.pixelColor(24, y).lightness() for y in range(131, 135))
    pale = min(canvas.pixelColor(24, y).lightness() for y in range(54, 58))
    assert dark < pale < 190
    assert canvas.pixelColor(305, 90) == QColor("white")
    panel.close()


def test_theme4_color_switch_tints_book_and_prices(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / "config.json")
    config = store.update({"display_theme": "theme4", "symbols": ["600000"], "show_title_buttons": True,
                           "stock_name_color": "#3355ff", "color_scheme": "cn"})
    window = TickerWindow(config)
    panel = window.docked_book
    window.grayscale_requested.connect(lambda: window.apply_config(store.update({"grayscale": not store.get().grayscale})))
    window.update_snapshot(Snapshot("mock", [Quote.from_prices("600000", "示例", 100, 99)]), "")
    window.update_depth(DepthSnapshot("600000", (DepthLevel("bid", 99.5, 100),
                                                DepthLevel("bid", 99.9, 30), DepthLevel("ask", 100.5, 50)),
                                      available=True, depth_mode=DEPTH_FULL))
    colors, widths, labels = [], [], {}
    draw_lines, draw_text = QPainter.drawLines, QPainter.drawText

    def record_lines(painter, lines):
        colors.append(painter.pen().color())
        widths.extend([painter.pen().widthF()] * len(lines))
        return draw_lines(painter, lines)

    def record_text(painter, point, text):
        labels[text] = painter.pen().color()
        return draw_text(painter, point, text)

    monkeypatch.setattr(QPainter, "drawLines", record_lines)
    monkeypatch.setattr(QPainter, "drawText", record_text)
    try:
        panel.grab()
        assert all(color.saturation() == 0 for color in colors + list(labels.values()))
        assert [color.alpha() for color in colors] == [255] * 3
        assert widths == [config.depth_line_width] * 3
        window.apply_config(store.update({"theme4_depth_opacity": .35}))
        colors.clear()
        widths.clear()
        panel.grab()
        assert [color.alphaF() for color in colors] == pytest.approx([.35] * 3, abs=1 / 255)
        assert labels["100.00"].alpha() == 255
        window.title_bar.grayscale_button.click()
        colors.clear()
        widths.clear()
        panel.grab()
        assert widths == [config.depth_line_width] * 3
        assert [color.alphaF() for color in colors] == pytest.approx([.35] * 3, abs=1 / 255)
        tinted = [color for color in colors if color.saturation() > 0]
        bids = [color for color in tinted if color.red() > color.green()]
        assert len(bids) == 2 and bids[0].lightness() > bids[1].lightness()
        assert any(color.green() > color.red() for color in tinted)
        assert labels["示例"] == QColor("#3355ff") and labels["100.00"].red() > labels["100.00"].green()
        assert not Store(store.path).get().grayscale
        window.apply_config(store.update({"theme4_depth_opacity": 0}))
        colors.clear()
        labels.clear()
        panel.grab()
        assert not colors and labels["100.00"].alpha() == 255
        assert panel._receipts["600000"][1] is False
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("dark", [True, False])
def test_gray_order_and_rounded_references(dark):
    assert volume_gray(0, dark) > volume_gray(.5, dark) > volume_gray(1, dark)
    assert reference_prices(398.72) == (402.71, 394.73)
    assert reference_prices(10.50) == (10.61, 10.40)
