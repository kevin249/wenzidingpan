"""紧凑主题：实际行情、悬停详情、字号透明度与快捷隐藏。"""

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QCursor, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stockwidget import mcp_bs, tdw
from stockwidget.config import Store, sanitize
from stockwidget.intraday import Trend
from stockwidget.poller import Snapshot
from stockwidget.providers.base import Quote
from stockwidget.ui import tdw_overlay
from stockwidget.ui.window import TickerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def quotes():
    return [Quote.from_prices("603986", "兆易创新", 243.18, 241.15),
            Quote.from_prices("300364", "中文在线", 32.16, 31.49),
            Quote.from_prices("688561", "奇安信", 48.72, 48.90)]


@pytest.mark.parametrize("theme", ["depth", "radar"])
def test_compact_themes_stream_and_hide_with_their_popup(app, theme):
    config = sanitize({"display_theme": theme, "symbols": [q.symbol for q in quotes()]})
    window = TickerWindow(config)
    try:
        window.restore_bounds(None, [])
        window.show()
        window._sync_rows(quotes())
        app.processEvents()
        assert window.stealth.isVisible()
        assert not window.title_bar.isVisible()
        assert not window.focus_host.isVisible()
        assert not window.grip.isVisible()
        assert window.width() < 600
        assert window.height() < (45 if theme == "depth" else 130)
        assert not any(row.isVisible() for row in window._rows.values())
        QTest.mouseMove(window.stealth, window.stealth.cells[0][0].center())
        app.processEvents()
        assert window.stealth.peek.isVisible()
        q = quotes()
        q[0] = Quote.from_prices("603986", "兆易创新", 243.20, 241.15)
        trend = Trend(prices=[242.9, 243.0, 243.2], minutes=["10:54", "10:55", "10:56"])
        window.update_snapshot(Snapshot(provider_id="mock", quotes=q, trends={"603986": trend},
                                        updated_symbols=("603986",)), "mock")
        window.stealth.show_peek("603986")
        app.processEvents()
        assert window.stealth.peek.isVisible()
        assert window.stealth.selected_quote().price == 243.20
        assert window.stealth.series("603986")[0][-1] == 243.2
        window.hide()
        app.processEvents()
        assert not window.stealth.peek.isVisible()
        window.show()
        app.processEvents()
        hide = next(a for a in window.build_menu().actions() if a.text().startswith("隐藏窗口"))
        window.stealth.show_peek("603986")
        assert window.stealth.peek.isVisible()
        hide.trigger()
        assert not window.isVisible() and not window.stealth.peek.isVisible()
        window.show()
        app.processEvents()
        assert not window.stealth.peek.isVisible()
        window.stealth.show_peek("603986")
        QCursor.setPos(window.mapToGlobal(QPoint(-80, -80)))
        window.stealth._dismiss_if_outside()
        assert not window.stealth.peek.isVisible()
    finally:
        window.close()


@pytest.mark.parametrize("theme", ["depth", "radar"])
def test_font_opacity_click_through_and_theme_switch(app, theme):
    config = sanitize({"display_theme": theme})
    window = TickerWindow(config)
    try:
        window.show()
        window._sync_rows(quotes())
        app.processEvents()
        before = window.size()
        window.apply_config(replace(config, font_size=20, opacity=.35))
        app.processEvents()
        assert window.stealth.font().pixelSize() == 20
        assert window.width() > before.width()
        assert window.height() > before.height()
        assert window.stealth.graphicsEffect().opacity() == pytest.approx(.35)
        assert window.stealth.peek.windowOpacity() == pytest.approx(.35, abs=.01)
        window.stealth.show_peek("603986")
        window.apply_config(replace(config, click_through=True))
        window.stealth.show_peek("603986")
        assert not window.stealth.peek.isVisible()
        window.apply_config(sanitize({"display_theme": "theme3"}))
        app.processEvents()
        assert not window.stealth.isVisible()
        assert window.focus_host.isVisible()
        assert all(row.isVisible() for row in window._rows.values())
    finally:
        window.close()


@pytest.mark.parametrize("theme", ["depth", "radar"])
def test_compact_popup_only_draws_outer_local_tdw_crossings(app, theme, monkeypatch):
    monkeypatch.setattr(mcp_bs, "get_source", lambda: mcp_bs.SOURCE_MCP)
    monkeypatch.setattr(mcp_bs, "bs_points", lambda *args, **kwargs: [(1, "S")])
    arrows, captions, other_marks = [], [], []
    draw_exit = tdw_overlay.draw_tdw_exit
    draw_text = QPainter.drawText
    def record(painter, x, price_y, tdw_y, paint_color):
        arrows.append((x, price_y, tdw_y))
        return draw_exit(painter, x, price_y, tdw_y, paint_color)
    def record_text(painter, *args):
        captions.extend(arg for arg in args if isinstance(arg, str))
        return draw_text(painter, *args)
    monkeypatch.setattr(tdw_overlay, "draw_tdw_exit", record)
    monkeypatch.setattr(tdw_overlay, "draw_event", lambda *args, **kwargs: other_marks.append(args))
    monkeypatch.setattr(QPainter, "drawText", record_text)

    quote = quotes()[0]
    date = "2026-11-06"
    trend = Trend(prices=[243.0, 242.9, 243.1, 243.2, 243.0],
                  minutes=["10:54", "10:55", "10:56", "10:57", "10:58"],
                  trade_date=date, tdw_points={
                      f"{date} 10:54": tdw.Point(.24, 243.0, ()),
                      f"{date} 10:55": tdw.Point(.15, 242.9, ("B1",)),
                      f"{date} 10:56": tdw.Point(.6, 243.1, ("B2",)),
                      f"{date} 10:57": tdw.Point(3.6, 243.2, ("S2",)),
                      f"{date} 10:58": tdw.Point(3.1, 243.0, ("S1",)),
                  })
    config = sanitize({"display_theme": theme, "symbols": [quote.symbol], "show_bs_points": True})
    window = TickerWindow(config)
    try:
        window.show()
        window._sync_rows([quote], {quote.symbol: trend})
        app.processEvents()
        assert not arrows
        window.stealth.show_peek(quote.symbol)
        arrows.clear()
        captions.clear()
        window.stealth.peek.grab()
        assert len(arrows) == 2
        assert 0 < arrows[0][0] < arrows[1][0]
        assert [price_y for _, price_y, _ in arrows] == pytest.approx([30, 0])
        assert [tdw_y for _, _, tdw_y in arrows] == pytest.approx([
            32 + tdw_overlay.power_y(.15, 16), 32 + tdw_overlay.power_y(3.6, 16),
        ])
        assert not other_marks
        assert not any(tag in text for text in captions for tag in ("S1", "S2", "B1", "B2"))
    finally:
        window.close()


def test_profile_migration_is_once_only_and_keeps_common_settings(tmp_path):
    store = Store(tmp_path / "config.json")
    store.update({"display_theme": "depth", "symbols": ["600519"], "font_size": 25,
                  "background_alpha": .8, "opacity": .95,
                  "bounds": {"x": 52, "y": 70, "width": 1000, "height": 620, "scale": 2.0}})
    store.update_theme_profile("theme3", {"font_size": 18, "opacity": .85})
    store.migrate_stealth_themes()
    config = store.get()
    assert config.font_size == 12 and config.background_alpha == 0
    assert config.bounds.x == 52 and config.bounds.scale == 1
    assert config.symbols == ["600519"]
    assert config.theme_profiles["theme3"]["font_size"] == 18
    store.update({"font_size": 14, "opacity": .4})
    store.update({"display_theme": "radar", "font_size": 11, "opacity": .65})
    reopened = Store(store.path)
    reopened.migrate_stealth_themes()
    assert reopened.get().font_size == 11
    depth = reopened.update({"display_theme": "depth"})
    assert depth.font_size == 14 and depth.opacity == .4


@pytest.mark.parametrize("theme", ["depth", "radar"])
def test_overflow_can_scroll_to_the_last_stock(app, theme):
    config = sanitize({"display_theme": theme})
    window = TickerWindow(config)
    try:
        many = [Quote.from_prices(f"{600000+i}", f"股票{i}", 10 + i, 10) for i in range(50)]
        window.show()
        window._sync_rows(many)
        app.processEvents()
        panel = window.stealth
        assert panel._max_scroll() > 0
        event = QWheelEvent(QPointF(10, 10), QPointF(panel.mapToGlobal(QPoint(10, 10))),
                            QPoint(), QPoint(0, -120000), Qt.NoButton, Qt.NoModifier,
                            Qt.NoScrollPhase, False)
        QApplication.sendEvent(panel, event)
        app.processEvents()
        assert panel._scroll_offset > 0
        assert panel.rect().intersects(panel.cells[-1][0])
    finally:
        window.close()
