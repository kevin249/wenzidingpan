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


def test_dark_fund_always_uses_the_yi_unit(app):
    """暗盘金额统一折算成亿，不足一亿也写成 0.XX 亿，单位不再在万和亿之间跳。"""
    from stockwidget.ui.theme import fmt_money

    assert fmt_money(198_000_000) == "+1.98亿"
    assert fmt_money(-952_000_000) == "-9.52亿"
    assert fmt_money(-43_800_000) == "-0.44亿"
    assert fmt_money(9_650_000) == "+0.10亿"
    assert fmt_money(120_000) == "+0.00亿"
    assert fmt_money(0) == "0.00亿"
    assert fmt_money(None) is None


def test_quote_row_shows_dark_fund_in_yi(app):
    from stockwidget.providers.base import Quote

    config = Config()
    quote = Quote.from_prices("000001", "平安银行", 12.34, 12.00)
    quote.dark_fund = -43_800_000
    row = QuoteRow("000001")
    row.resize(360, 120)
    row.apply_config(config)
    row.update_quote(quote, config)

    assert row.dark_value.text() == "-0.44亿"
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


def test_stacked_row_style_keeps_top_and_bottom_texts_on_one_line(app):
    """上中下：名称与现价同行，暗盘与涨跌幅同行，中间是走势图。"""
    row = QuoteRow("600519")
    row.resize(480, 220)
    row.apply_config(Config(row_style="stacked"))

    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.name_label))[:2] == (0, 0)
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 1)
    # 走势图独占中间一行，横跨左右两列
    assert layout.getItemPosition(layout.indexOf(row.sparkline)) == (1, 0, 1, 2)
    assert layout.getItemPosition(layout.indexOf(row.dark_box))[:2] == (2, 0)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (2, 1)
    row.close()


def test_stacked_row_style_never_splits_a_line_even_when_narrow(app):
    """显式选了上中下，窄格子里也不能把同一行的两段文字拆开。"""
    row = QuoteRow("600519")
    row.resize(150, 150)
    row.apply_config(Config(row_style="stacked"))
    row.name_label.setText("一只名字很长的股票")
    row.price_label.setText("1234.56")
    row.dark_value.setText("-9.52亿")
    row.percent_label.setText("-6.34%")
    row._update_layout_mode()

    layout = row.layout()
    assert row._narrow is True
    assert layout.getItemPosition(layout.indexOf(row.name_label))[:2] == (0, 0)
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 1)
    assert layout.getItemPosition(layout.indexOf(row.dark_box))[:2] == (2, 0)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (2, 1)
    row.close()


def test_sides_row_style_keeps_two_lines_on_each_side(app):
    """左中右：左右各两行，走势图纵向占满中间列。"""
    row = QuoteRow("600519")
    row.resize(480, 220)
    row.apply_config(Config(row_style="sides"))

    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.name_label))[:2] == (0, 0)
    assert layout.getItemPosition(layout.indexOf(row.dark_box))[:2] == (1, 0)
    assert layout.getItemPosition(layout.indexOf(row.sparkline)) == (0, 1, 2, 1)
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 2)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (1, 2)
    row.close()


def test_chart_height_overrides_the_font_derived_height(app):
    row = QuoteRow("600519")
    row.resize(480, 220)
    row.apply_config(Config(font_size=13))
    automatic = row.sparkline.sizeHint().height()
    assert row.sparkline.maximumHeight() == 16777215  # 自动模式下不封顶

    row.apply_config(Config(font_size=13, chart_height=90))
    assert row.sparkline.sizeHint().height() == 90
    assert row.sparkline.maximumHeight() == 90  # 有空间时就是这么高，不再更高
    # 不留硬下限：窗口塞不下时走势图一路压扁让位，不会把行顶出可视区
    assert row.sparkline.minimumHeight() == 0
    assert row.sizeHint().height() > automatic

    # 改回 0 应恢复成按字号推算，并解除高度上限
    row.apply_config(Config(font_size=13))
    assert row.sparkline.sizeHint().height() == automatic
    assert row.sparkline.minimumHeight() == 0
    assert row.sparkline.maximumHeight() == 16777215
    row.close()


def test_row_gets_the_requested_chart_height_when_there_is_room(app):
    """有地方放时，走势图就得正好是设定的高度。"""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    row = QuoteRow("600519")
    layout.addWidget(row)
    host.resize(480, 240)
    row.apply_config(Config(chart_height=90))
    host.show()
    app.processEvents()

    assert row.sparkline.height() == 90
    host.close()


def test_changing_chart_height_grows_a_manually_sized_window(app):
    """手动缩过外框后，调高 K 线仍应补足空间，不能被旧窗口高度压回去。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
    ])
    window.show()
    app.processEvents()

    old_width = window.width()
    old_height = window.height()
    window._manual_size = True
    window.apply_config(Config(visible_rows=2, chart_height=90))
    app.processEvents()

    row = next(iter(window._rows.values()))
    assert window.width() == old_width
    assert window.height() > old_height
    assert row.sparkline.height() == 90
    window.close()


def test_tall_chart_never_pushes_rows_out_of_the_viewport(app):
    """组件刻意不带滚动条，所以内容再高也必须压得进可视区。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=4, chart_height=400))
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        Quote.from_prices("300223", "北京君正", 381.66, 386.48),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
    ])
    window.show()
    app.processEvents()

    viewport = window.scroll.viewport()
    assert window.rows_host.height() <= viewport.height()
    assert window.scroll.verticalScrollBar().isVisible() is False
    for row in window._rows.values():
        bottom = row.mapTo(viewport, row.rect().bottomLeft()).y()
        assert bottom <= viewport.height()
    window.close()


def test_hidden_sparkline_ignores_chart_height(app):
    row = QuoteRow("600519")
    row.resize(480, 220)
    row.apply_config(Config(show_sparkline=False, chart_height=120))

    assert row.sparkline.sizeHint().height() == 0
    assert row.sparkline.minimumHeight() == 0
    assert row.sparkline.maximumHeight() == 16777215
    row.close()


def test_switching_back_to_sides_widens_the_window_again(app):
    """自动宽度是照着 QuoteRow 的 sizeHint 算的，上中下会把它算窄；
    切回左中右时必须重新撑开，否则格子太窄又被判成窄卡片，样式就再也切不回来。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    quotes = [
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
    ]
    window._sync_rows(quotes)
    window.show()
    app.processEvents()

    window.apply_config(Config(visible_rows=2, row_style="stacked"))
    app.processEvents()
    row = next(iter(window._rows.values()))
    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.sparkline)) == (1, 0, 1, 2)

    window.apply_config(Config(visible_rows=2, row_style="sides"))
    app.processEvents()
    assert layout.getItemPosition(layout.indexOf(row.sparkline)) == (0, 1, 2, 1)
    assert all(row._narrow is False for row in window._rows.values())
    window.close()


def test_stacked_rows_stay_reachable_when_the_row_count_is_high(app):
    """上中下每格多一行走势图，行数一多更容易顶出屏幕；
    走势图必须能一路压扁让位，文字不能被挤出可视区。"""
    from stockwidget.providers.base import Quote

    count = 12
    quotes = []
    for index in range(count):
        quote = Quote.from_prices(f"6005{index:02d}", f"股票{index:02d}", 1304.66, 1272.83)
        quote.dark_fund = 198_000_000
        quotes.append(quote)

    window = TickerWindow(Config(visible_rows=count, row_style="stacked"))
    window._sync_rows(quotes)
    window.show()
    app.processEvents()

    viewport = window.scroll.viewport()
    assert window.rows_host.height() <= viewport.height()
    for row in window._rows.values():
        label = row.percent_label
        assert label.mapTo(viewport, label.rect().bottomLeft()).y() <= viewport.height()
    window.close()


def test_width_only_drag_keeps_scale_when_content_is_screen_compressed(app):
    """自然高度超屏时窗口已被压回屏幕内，不能再拿那个高度当缩放分母，
    否则用户只拖宽度也会被判成缩小，字和图一起变小。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=4, chart_height=400))
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        Quote.from_prices("300223", "北京君正", 381.66, 386.48),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
    ])
    window.show()
    app.processEvents()
    screen = window.screen().availableGeometry()
    assert window._base_frame_height(window.width()) > screen.height()  # 确实超屏

    height = window.height()
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(QSize(window.width() + 60, height))
    window._apply_scale()
    assert window._scale == pytest.approx(1.0)

    # 真的拖高度时仍要跟着缩放
    window._on_grip_dragged(QSize(window.width(), round(height * 0.7)))
    window._apply_scale()
    assert window._scale < 1.0
    window.close()


def test_window_scales_fixed_chart_height_with_the_frame(app):
    window = TickerWindow(Config(chart_height=40))
    window._scale = 2.0
    assert window.scaled_config().chart_height == 80

    # 配置里 8–400 只是存盘范围，缩放后的显示值不该被它卡住，
    # 否则边界上的高度会和周围文字缩得不一样。
    window._config = Config(chart_height=400)
    assert window.scaled_config().chart_height == 800
    window._scale = 0.6
    window._config = Config(chart_height=8)
    assert window.scaled_config().chart_height == 5

    window._config = Config(chart_height=0)
    assert window.scaled_config().chart_height == 0  # 自动高度不参与缩放
    window.close()


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


def test_title_buttons_can_be_hidden(app):
    """关掉右上角按钮后整条标题栏收起，那一行高度也还给行情内容。"""
    from stockwidget.providers.base import Quote

    quotes = [Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)]

    window = TickerWindow(Config(visible_rows=1))
    window._sync_rows(quotes)
    window.show()
    app.processEvents()
    # 行控件显示后 sizeHint 会再长一次，先按显示后的尺寸重算一遍当基准。
    window.apply_config(Config(visible_rows=1))
    app.processEvents()
    assert window.title_bar.isVisible() is True
    with_bar = window.height()

    window.apply_config(Config(visible_rows=1, show_title_buttons=False))
    app.processEvents()

    assert window.title_bar.isVisible() is False
    for button in (
        window.title_bar.refresh_button,
        window.title_bar.settings_button,
        window.title_bar.grayscale_button,
        window.title_bar.quit_button,
    ):
        assert button.isVisible() is False
    # 收起的标题栏不再占位：窗口应当矮下去，而不是留一条空白。
    assert window.height() < with_bar
    assert window.height() == pytest.approx(
        with_bar - window.title_bar.sizeHint().height(), abs=2
    )
    # 行情内容照常显示，没被一起藏掉。
    assert window.scroll.isVisible() is True
    assert window._rows["600519"].isVisible() is True

    # 再打开就该原样回来。
    window.apply_config(Config(visible_rows=1))
    app.processEvents()
    assert window.title_bar.isVisible() is True
    assert window.title_bar.quit_button.isVisible() is True
    assert window.height() == pytest.approx(with_bar, abs=2)
    window.close()


def test_hidden_title_buttons_keep_window_draggable_and_point_to_tray(app):
    """标题栏是窗口自带的拖拽把手，藏起来后内容区必须还能拖动。"""
    window = TickerWindow(Config(show_title_buttons=False))
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
            QEvent.MouseMove, press_at + QPoint(60, 40), buttons=Qt.LeftButton
        ),
    )

    assert moved is True
    assert window.frameGeometry().topLeft() == start + QPoint(60, 40)
    # 空列表的提示不能再让用户去点已经藏起来的 ⚙。
    assert "⚙" not in window.empty_label.text()
    assert "托盘" in window.empty_label.text()
    window.close()
