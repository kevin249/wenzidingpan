"""桌面网格布局回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.ui.marquee import Marquee
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.sparkline import Sparkline
from stockwidget.ui.window import ResizeGrip, TickerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_quote_row_stacks_price_and_percent_in_two_rows(app):
    from stockwidget.providers.base import Quote

    row = QuoteRow("600519")
    config = Config()
    row.resize(300, 120)
    row.apply_config(config)
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    quote.dark_fund = 198_000_000
    row.update_quote(quote, config)

    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 2)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (1, 2)
    assert row.percent_label.text() == "+2.50%"
    black = "color: rgba(0,0,0,255);"
    assert row.name_label.styleSheet() == black
    assert row.dark_label.styleSheet() == black
    assert row.dark_value.styleSheet() == black
    assert row.price_label.styleSheet() != black


def test_quote_row_uses_independent_font_sizes_and_aligns_second_row(app):
    config = Config(
        stock_name_font_size=14,
        stock_price_font_size=20,
        stock_percent_font_size=13,
        dark_trade_font_size=12,
        chart_label_font_size=8,
    )
    row = QuoteRow("600519")
    row.resize(220, 180)
    row.apply_config(config)

    layout = row.layout()
    assert row._narrow is True
    assert row.name_label.font().pixelSize() == 14
    assert row.name_label.font().bold() is False
    assert row.price_label.font().pixelSize() == 20
    assert row.price_label.font().bold() is True
    assert row.percent_label.font().pixelSize() == 13
    assert row.dark_label.font().pixelSize() == 12
    assert row.dark_value.font().pixelSize() == 12
    assert row.sparkline._annotation_font.pixelSize() == 8
    assert layout.getItemPosition(layout.indexOf(row.dark_box))[:2] == (2, 0)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (2, 1)


def test_quote_row_uses_independent_font_colors_and_weights(app):
    from stockwidget.providers.base import Quote

    config = Config(
        stock_name_color="#112233",
        stock_price_color="#223344",
        stock_percent_color="#334455",
        dark_trade_color="#445566",
        stock_name_bold=True,
        stock_price_bold=False,
        stock_percent_bold=True,
        dark_trade_bold=True,
    )
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    quote.dark_fund = 198_000_000
    row = QuoteRow("600519")
    row.apply_config(config)
    row.update_quote(quote, config)

    assert row.name_label.styleSheet() == "color: rgba(17,34,51,255);"
    assert row.price_label.styleSheet() == "color: rgba(34,51,68,255);"
    assert row.percent_label.styleSheet() == "color: rgba(51,68,85,255);"
    assert row.dark_label.styleSheet() == "color: rgba(68,85,102,255);"
    assert row.dark_value.styleSheet() == row.dark_label.styleSheet()
    assert row.name_label.font().bold() is True
    assert row.price_label.font().bold() is False
    assert row.percent_label.font().bold() is True
    assert row.dark_label.font().bold() is True
    assert row.dark_value.font().bold() is True
    row.close()


def test_marquee_uses_each_text_style_in_single_mode(app):
    from stockwidget.providers.base import Quote

    config = Config(
        stock_name_font_size=12,
        stock_price_font_size=18,
        stock_percent_font_size=14,
        dark_trade_font_size=10,
        stock_name_color="#112233",
        stock_price_color="#223344",
        stock_percent_color="#334455",
        dark_trade_color="#445566",
        stock_name_bold=True,
        stock_price_bold=False,
        stock_percent_bold=True,
        dark_trade_bold=True,
    )
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    quote.dark_fund = 198_000_000
    marquee = Marquee()
    marquee.apply_config(config)
    marquee.set_quotes([quote])

    assert [segment.font_size for segment in marquee._segments] == [12, 18, 14, 10]
    assert [segment.bold for segment in marquee._segments] == [True, False, True, True]
    assert [segment.color.name() for segment in marquee._segments] == [
        "#112233",
        "#223344",
        "#334455",
        "#445566",
    ]
    assert marquee._segments[2].text == "+2.50%"
    marquee.close()


def test_quote_row_removes_chart_column_when_sparkline_is_hidden(app):
    row = QuoteRow("600519")
    row.resize(480, 220)
    row.apply_config(Config(show_sparkline=False, font_size=18))

    layout = row.layout()
    assert layout.indexOf(row.sparkline) == -1
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 1)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (1, 1)
    assert layout.columnStretch(1) == 0
    assert layout.columnStretch(2) == 1

    row.apply_config(Config(show_sparkline=True, font_size=18))
    assert layout.indexOf(row.sparkline) >= 0
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 2)
    assert layout.columnStretch(1) == 1
    assert layout.columnStretch(2) == 0


def test_sparkline_fill_is_disabled_by_default_and_can_be_enabled(app):
    row = QuoteRow("600519")

    row.apply_config(Config())
    assert row.sparkline._show_fill is False

    row.apply_config(Config(show_sparkline_fill=True))
    assert row.sparkline._show_fill is True
    row.close()


def test_sparkline_fill_switch_changes_area_below_curve(app):
    sparkline = Sparkline()
    sparkline.resize(120, 60)
    sparkline.set_series([10.0, 20.0])

    def area_pixel(fill_enabled):
        sparkline.set_annotations(
            None,
            [],
            show_signals=False,
            show_open_line=False,
            show_high_low=False,
            show_fill=fill_enabled,
            grayscale=False,
        )
        image = QImage(sparkline.size(), QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        sparkline.render(image)
        return image.pixelColor(60, 50)

    without_fill = area_pixel(False)
    with_fill = area_pixel(True)
    assert with_fill != without_fill
    sparkline.close()


def test_one_row_adapts_to_viewport_without_scrollbars(app):
    """一行放不下时每格切换窄布局，不能生成横向或纵向滚动条。"""
    config = Config(visible_rows=1)
    window = TickerWindow(config)
    symbols = ["600519", "000001", "300750"]
    for symbol in symbols:
        row = QuoteRow(symbol)
        row.apply_config(config)
        row.name_label.setText("一只名字很长的股票")
        row.price_label.setText("1234.56")
        window._rows[symbol] = row

    window._lay_out_grid(symbols)
    window.scroll.setVisible(True)
    window.empty_label.setVisible(False)
    window.resize(390, 140)
    window.show()
    app.processEvents()

    assert window.scroll.horizontalScrollBar().isVisible() is False
    assert window.scroll.verticalScrollBar().isVisible() is False
    assert window.rows_host.width() == window.scroll.viewport().width()
    assert all(row._narrow for row in window._rows.values())

    window.close()


class _MouseEvent:
    def __init__(self, position, button=Qt.NoButton, buttons=Qt.NoButton):
        self._position = QPointF(*position) if isinstance(position, tuple) else QPointF(position)
        self._button = button
        self._buttons = buttons

    def globalPosition(self):
        return self._position

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def accept(self):
        pass


class _WindowDragEvent(_MouseEvent):
    def __init__(self, event_type, position, button=Qt.NoButton, buttons=Qt.NoButton):
        super().__init__(position, button=button, buttons=buttons)
        self._event_type = event_type

    def type(self):
        return self._event_type


def test_resize_grip_changes_width_and_height(app):
    window = TickerWindow(Config())
    window.resize(300, 240)
    grip = ResizeGrip(window)
    sizes = []
    grip.dragged.connect(sizes.append)

    grip.mousePressEvent(_MouseEvent((500, 400), button=Qt.LeftButton))
    grip.mouseMoveEvent(_MouseEvent((570, 445), buttons=Qt.LeftButton))

    assert sizes == [QSize(370, 285)]
    window.close()


def test_scale_uses_relative_drag_width_and_keeps_user_size(app):
    window = TickerWindow(Config())
    window.resize(600, 240)  # 多列网格的自然宽度可能远大于 300。
    window._on_grip_drag_started(window.size())
    dragged_size = QSize(660, 264)

    window._on_grip_dragged(dragged_size)
    window._apply_scale()

    assert window.size() == dragged_size
    assert window._scale == pytest.approx(1.1)
    assert window.scaled_config().font_size == 14
    window.close()


def test_window_scale_preserves_independent_font_ratios(app):
    config = Config(
        font_size=10,
        stock_name_font_size=12,
        stock_price_font_size=18,
        stock_percent_font_size=11,
        dark_trade_font_size=9,
        chart_label_font_size=8,
    )
    window = TickerWindow(config)
    window._scale = 2.0

    scaled = window.scaled_config()
    assert scaled.font_size == 20
    assert scaled.stock_name_font_size == 24
    assert scaled.stock_price_font_size == 36
    assert scaled.stock_percent_font_size == 22
    assert scaled.dark_trade_font_size == 18
    assert scaled.chart_label_font_size == 16
    window.close()


def test_grip_uses_height_for_content_scale(app):
    window = TickerWindow(Config())
    window.resize(400, 200)
    window._on_grip_drag_started(window.size())

    window._on_grip_dragged(QSize(600, 160))
    window._apply_scale()

    assert window._scale == pytest.approx(0.8)
    assert window.scaled_config().font_size == 10
    window.close()


def test_restored_large_frame_repairs_stale_small_label_scale(app):
    from stockwidget.config import Bounds
    from stockwidget.providers.base import Quote

    config = Config(visible_rows=1, font_size=9)
    window = TickerWindow(config)
    screen = app.primaryScreen().availableGeometry()
    window.restore_bounds(
        Bounds(0, 0, 760, 241, scale=0.696, manual_size=True),
        [screen],
    )
    window._sync_rows(
        [
            Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
            Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
            Quote.from_prices("300223", "北京君正", 381.66, 386.48),
        ]
    )
    app.processEvents()

    assert window._scale > 1.0
    assert all(row.name_label.font().pixelSize() > 9 for row in window._rows.values())
    assert all(row.price_label.font().pixelSize() > 9 for row in window._rows.values())
    assert all(row.percent_label.font().pixelSize() > 7 for row in window._rows.values())
    window.close()


def test_small_manual_frame_keeps_all_scrollbars_disabled(app):
    from stockwidget.config import Bounds
    from stockwidget.providers.base import Quote

    config = Config(
        visible_rows=1,
        font_size=10,
        stock_name_font_size=10,
        stock_price_font_size=12,
        stock_percent_font_size=8,
        dark_trade_font_size=8,
        chart_label_font_size=7,
    )
    window = TickerWindow(config)
    screen = app.primaryScreen().availableGeometry()
    window.restore_bounds(Bounds(0, 0, 382, 129, manual_size=True), [screen])
    quotes = [
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        Quote.from_prices("300223", "北京君正", 381.66, 386.48),
    ]
    for quote, dark_fund in zip(quotes, (198_000_000, -952_000_000, -48_200_000)):
        quote.dark_fund = dark_fund
    window._sync_rows(quotes)
    window.show()
    app.processEvents()

    bar = window.scroll.horizontalScrollBar()
    assert bar.isVisible() is False
    assert window.scroll.verticalScrollBar().isVisible() is False
    assert window.rows_host.width() == window.scroll.viewport().width()
    assert all(row._narrow for row in window._rows.values())
    assert all(
        row.name_label.geometry().width() >= row.name_label.minimumSizeHint().width()
        for row in window._rows.values()
    )
    assert all(
        row.price_label.geometry().width() >= row.price_label.minimumSizeHint().width()
        for row in window._rows.values()
    )
    assert all(
        row.dark_box.geometry().width() >= row.dark_box.minimumSizeHint().width()
        for row in window._rows.values()
        if row.dark_box.isVisible()
    )
    assert all(
        row.percent_label.geometry().width() >= row.percent_label.minimumSizeHint().width()
        for row in window._rows.values()
    )
    window.close()


def test_zero_background_alpha_keeps_grid_surface_fully_transparent(app):
    config = Config(background_alpha=0.0, visible_rows=1)
    window = TickerWindow(config)
    window.scroll.setVisible(True)
    window.empty_label.setVisible(False)
    window.resize(390, 140)
    window.show()
    app.processEvents()

    assert window.rows_host.autoFillBackground() is False
    image = window.grab().toImage()
    point = window.scroll.mapTo(window, QPoint(2, 2))
    assert image.pixelColor(point).alpha() == 0
    window.close()


def test_window_opacity_uses_qt_content_effect(app):
    window = TickerWindow(Config(opacity=0.35))

    assert window.windowOpacity() == pytest.approx(1.0)
    assert all(effect.opacity() == pytest.approx(0.35) for effect in window._opacity_effects)

    window.apply_config(Config(opacity=0.7))
    assert all(effect.opacity() == pytest.approx(0.7) for effect in window._opacity_effects)
    assert window.handle._opacity == pytest.approx(0.7)
    window.close()


def test_window_opacity_also_updates_background_alpha(app):
    window = TickerWindow(Config(opacity=0.25, background_alpha=0.8))
    window.resize(400, 180)
    window.show()
    app.processEvents()
    first_alpha = window.grab().toImage().pixelColor(30, 30).alpha()

    window.apply_config(Config(opacity=0.75, background_alpha=0.8))
    app.processEvents()
    second_alpha = window.grab().toImage().pixelColor(30, 30).alpha()

    assert first_alpha == pytest.approx(255 * 0.25 * 0.8, abs=2)
    assert second_alpha == pytest.approx(255 * 0.75 * 0.8, abs=2)
    window.close()


def test_window_opacity_composites_name_price_and_percent_uniformly(app):
    from stockwidget.providers.base import Quote

    config = Config(
        opacity=0.25,
        background_alpha=0.0,
        visible_rows=1,
        show_sparkline=False,
    )
    window = TickerWindow(config)
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.resize(500, 160)
    window.show()
    app.processEvents()

    image = window.grab().toImage()
    row = window._rows["600519"]

    def maximum_alpha(widget):
        origin = widget.mapTo(window, QPoint(0, 0))
        return max(
            image.pixelColor(x, y).alpha()
            for y in range(origin.y(), origin.y() + widget.height())
            for x in range(origin.x(), origin.x() + widget.width())
        )

    alphas = [maximum_alpha(widget) for widget in (row.name_label, row.price_label, row.percent_label)]
    assert all(55 <= alpha <= 70 for alpha in alphas)
    assert max(alphas) - min(alphas) <= 2
    window.close()


def test_flush_bounds_emits_latest_position_size_and_scale(app):
    window = TickerWindow(Config())
    saved = []
    window.bounds_changed.connect(saved.append)
    window.setGeometry(123, 234, 567, 189)
    window._scale = 1.35
    window._manual_size = True
    window._save_timer.start()

    window.flush_bounds()

    assert window._save_timer.isActive() is False
    assert saved[-1] == {
        "x": 123,
        "y": 234,
        "width": 567,
        "height": 189,
        "scale": 1.35,
        "manual_size": True,
    }
    window.close()


def test_legacy_wide_bounds_do_not_inflate_scale_and_are_kept_visible(app):
    window = TickerWindow(Config())
    screen = QRect(0, 0, 1920, 1080)

    from stockwidget.config import Bounds

    window.restore_bounds(Bounds(1500, 900, 1039, 300), [screen])

    assert window._scale == 1.0
    assert window.geometry().right() <= screen.right()
    assert window.geometry().bottom() <= screen.bottom()
    window.close()


def test_saved_manual_scale_is_restored(app):
    from stockwidget.config import Bounds

    window = TickerWindow(Config())
    window.restore_bounds(
        Bounds(100, 100, 700, 260, scale=1.25, manual_size=True),
        [QRect(0, 0, 1920, 1080)],
    )

    assert window._scale == 1.25
    assert window._manual_size is True
    assert window.size() == QSize(700, 260)
    window.close()


def test_first_quotes_do_not_override_restored_manual_height(app):
    from stockwidget.config import Bounds
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=1))
    window.restore_bounds(
        Bounds(0, 0, 760, 130, scale=1.2, manual_size=True),
        [QRect(0, 0, 1920, 1080)],
    )
    window.show()
    app.processEvents()
    window._sync_rows(
        [
            Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
            Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
            Quote.from_prices("300223", "北京君正", 381.66, 386.48),
        ]
    )
    app.processEvents()

    assert window.height() == 130
    window.close()


def test_content_can_drag_window_when_click_through_is_disabled(app):
    window = TickerWindow(Config(click_through=False))
    window.move(100, 120)
    start = window.frameGeometry().topLeft()
    press_at = start + QPoint(40, 35)
    move_at = press_at + QPoint(80, 50)

    pressed = window.eventFilter(
        window.empty_label,
        _WindowDragEvent(QEvent.MouseButtonPress, press_at, button=Qt.LeftButton),
    )
    moved = window.eventFilter(
        window.empty_label,
        _WindowDragEvent(QEvent.MouseMove, move_at, buttons=Qt.LeftButton),
    )
    released = window.eventFilter(
        window.empty_label,
        _WindowDragEvent(QEvent.MouseButtonRelease, move_at, button=Qt.LeftButton),
    )

    assert pressed is False
    assert moved is True
    assert released is True
    assert window.frameGeometry().topLeft() == start + QPoint(80, 50)
    window.close()


def test_click_through_disables_content_drag(app):
    window = TickerWindow(Config(click_through=True))
    window.move(100, 120)
    start = window.frameGeometry().topLeft()
    press_at = start + QPoint(40, 35)

    window.eventFilter(
        window.empty_label,
        _WindowDragEvent(QEvent.MouseButtonPress, press_at, button=Qt.LeftButton),
    )
    moved = window.eventFilter(
        window.empty_label,
        _WindowDragEvent(
            QEvent.MouseMove,
            press_at + QPoint(80, 50),
            buttons=Qt.LeftButton,
        ),
    )

    assert moved is False
    assert window.frameGeometry().topLeft() == start
    window.close()
