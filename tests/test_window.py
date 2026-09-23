"""桌面网格布局回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
    from PySide6.QtGui import QImage, QMouseEvent
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.intraday import Trend
from stockwidget.ui.depth_ladder import DEPTH_SHARE_MIN, OUTER_PAD
from stockwidget.ui.marquee import Marquee
from stockwidget.ui.quote_row import QuoteRow
from stockwidget.ui.sparkline import Sparkline
from stockwidget.ui.window import ResizeGrip, TickerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _StubClickWatcher:
    """ScreenClickWatcher 的测试替身：不装系统钩子、不吃真鼠标输入。

    走窗口层展开的用例一律先换上它——真 watcher 在 win32 上会装
    WH_MOUSE_LL，测试机器上用户的真实点击会顺着钩子把展开态收掉，用例就悬了。
    """

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _install_stub_watcher(window) -> _StubClickWatcher:
    stub = _StubClickWatcher()
    window._screen_click_watcher = stub
    return stub


def _click_away(window) -> None:
    """模拟「点屏幕任意位置」：给一个必然不在任何价格热区上的全局坐标。"""
    window._on_screen_click(QPoint(-9_999, -9_999))


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


def test_theme2_click_expands_chart_inside_the_row_without_touching_the_frame(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_sparkline=False)
    window = TickerWindow(config)
    quotes = [
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
        Quote.from_prices("300750", "宁德时代", 420.00, 410.00),
    ]
    window._sync_rows(
        quotes,
        {
            "600519": Trend(
                prices=[1275.0, 1290.0, 1304.66],
                high_price=1310.0,
                low_price=1268.0,
            )
        },
    )
    window.show()
    app.processEvents()
    geo = window.screen().availableGeometry()
    window.move(max(geo.left(), geo.right() - window.width() - 20), geo.top() + 20)
    app.processEvents()

    assert window._grid_size(3) == (3, 1)
    row = window._rows["600519"]
    assert row.theme2_header.isVisible() is True
    assert row.theme2_depth.isVisible() is True
    assert row.name_label.isVisible() is False
    assert row.theme2_depth.expanded is False

    watcher = _install_stub_watcher(window)
    before = window.geometry()
    row.theme2_depth.price_clicked.emit()
    app.processEvents()

    # 点一只＝全列表展开（用户口径 2026-09-23）：外框的位置与尺寸一个像素都不动。
    assert row.theme2_depth.expanded is True
    assert all(r.theme2_depth.expanded for r in window._rows.values())
    assert window.geometry() == before
    assert row.theme2_depth._layout().has_chart_band is True
    assert "600519" in row.theme2_code_label.full_text()
    assert "高 1310.00" in row.theme2_high_low_label.full_text()
    assert watcher.started == 1

    # 鼠标移开不消失：发一个真实的 Leave 事件也不能收起。
    app.sendEvent(row, QEvent(QEvent.Leave))
    app.processEvents()
    assert row.theme2_depth.expanded is True

    # 点屏幕任意位置（远离任何价格热区）→ 全部收起。
    _click_away(window)
    app.processEvents()
    assert row.theme2_depth.expanded is False
    assert all(not r.theme2_depth.expanded for r in window._rows.values())
    assert window.geometry() == before
    assert watcher.stopped == 1
    window.close()


def test_window_bell_tracks_and_clears_mcp_notifications(app):
    window = TickerWindow(Config(mcp_notifications_enabled=True))

    assert window.title_bar.bell_button.isHidden() is False
    window.show_mcp_notification("涨停提醒", "贵州茅台触发提醒")
    window.show_mcp_notification("风险提醒", "跌破保护价")

    assert window.title_bar.bell_button.text() == "BELL·2"
    assert "风险提醒" in window.title_bar.bell_button.toolTip()
    window.title_bar.bell_button.click()
    assert window.title_bar.bell_button.text() == "BELL"
    window.close()


def test_window_bell_announces_only_a_real_acknowledgement(app):
    """点掉 BELL 才算看过、才广播；藏起按钮只是设置变了，不能连累别的通道。"""
    window = TickerWindow(Config(mcp_notifications_enabled=True))
    cleared: list[bool] = []
    window.bell_cleared.connect(lambda: cleared.append(True))

    # 本来就没有未读时点它，不该惊动托盘
    window.title_bar.bell_button.click()
    assert cleared == []

    window.show_mcp_notification("涨停提醒", "贵州茅台触发提醒")
    window.title_bar.bell_button.click()
    assert cleared == [True]

    # 只关掉「窗口 BELL」这一路：按钮收起，但托盘的未读数是独立的，不能被抹掉
    cleared.clear()
    window.show_mcp_notification("风险提醒", "跌破保护价")
    window.apply_config(Config(mcp_notifications_enabled=True, mcp_bell_window=False))
    assert window.title_bar.bell_button.isHidden() is True
    assert cleared == []

    # 托盘那边确认时，窗口这边也得跟着清干净
    window.apply_config(Config(mcp_notifications_enabled=True))
    window.show_mcp_notification("涨停提醒", "再来一条")
    assert window.title_bar.bell_button.text() == "BELL·1"
    window.clear_mcp_notifications()
    assert window.title_bar.bell_button.text() == "BELL"
    window.close()


def test_window_bell_hides_on_its_own_switch_without_leaving_a_stale_count(app):
    """BELL 这一路单独关掉时按钮要收起，未读数也不能留到下次开回来。"""
    window = TickerWindow(Config(mcp_notifications_enabled=True))
    window.show_mcp_notification("涨停提醒", "贵州茅台触发提醒")
    assert window.title_bar.bell_button.text() == "BELL·1"

    window.apply_config(Config(mcp_notifications_enabled=True, mcp_bell_window=False))
    assert window.title_bar.bell_button.isHidden() is True

    window.apply_config(Config(mcp_notifications_enabled=True))
    assert window.title_bar.bell_button.isHidden() is False
    assert window.title_bar.bell_button.text() == "BELL"
    window.close()


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


def test_quote_row_collapses_every_text_color_into_one_gray(app):
    """灰度显示时连用户挑的固定色也一起变灰：整行文字只剩一个灰阶。

    否则设置在「股票名称颜色 / 股价颜色」里的彩色会漏出灰度模式，组件就不是「一个灰」了。
    """
    from stockwidget.providers.base import Quote

    config = Config(
        grayscale=True,
        grayscale_level=200,
        stock_name_color="#112233",
        stock_price_color="#223344",
        stock_percent_color="#334455",
        dark_trade_color="#445566",
    )
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    quote.dark_fund = 198_000_000
    row = QuoteRow("600519")
    row.apply_config(config)
    row.update_quote(quote, config)

    gray = "color: rgba(200,200,200,255);"
    assert row.name_label.styleSheet() == gray
    assert row.price_label.styleSheet() == gray
    assert row.percent_label.styleSheet() == gray
    assert row.dark_label.styleSheet() == gray
    assert row.dark_value.styleSheet() == gray
    # 主题2 顶栏走同一套判据，不许各留一份彩色。
    assert row.theme2_name_label.styleSheet() == gray
    assert row.theme2_code_label.styleSheet() == gray
    assert row.theme2_dark_label.styleSheet() == gray
    assert row.theme2_high_low_label.styleSheet() == gray
    # 走势图也拿到了灰阶（它只画灰，颜色由 _grayscale_level 决定）。
    assert row.sparkline._grayscale is True
    assert row.sparkline._grayscale_level == 200
    row.close()


def test_quote_row_keeps_configured_colors_while_grayscale_is_off(app):
    from stockwidget.providers.base import Quote

    config = Config(stock_name_color="#112233", stock_price_color="#223344")
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    row = QuoteRow("600519")
    row.apply_config(config)
    row.update_quote(quote, config)

    assert row.name_label.styleSheet() == "color: rgba(17,34,51,255);"
    assert row.price_label.styleSheet() == "color: rgba(34,51,68,255);"
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


class _WindowDragEvent(QMouseEvent):
    """真实的 :class:`QMouseEvent`，只是把 ``type()`` 换成调用方指定的那种。

    PySide6 6.11 的绑定做了参数类型校验：鸭子类型的事件对象传进
    ``QObject.eventFilter`` 会在 ``super().eventFilter(...)`` 处直接抛
    ``TypeError``（"called with wrong argument types"），测不出任何真实行为。
    """

    def __init__(self, event_type, position, button=Qt.NoButton, buttons=Qt.NoButton):
        local = QPointF(position)
        super().__init__(
            QEvent.MouseButtonPress, local, local, button, buttons, Qt.NoModifier
        )
        self._event_type = event_type

    def type(self):  # noqa: N802 - Qt 命名
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


def test_toggling_title_buttons_keeps_manual_frame_content_and_scale(app):
    """手动尺寸下切换标题栏，不能把缩放基准搞歪。

    标题栏是 chrome：外框不跟着加减那一条，内容区就凭空多／少一截高度，
    而绝对缩放基准（_absolute_scale_reference）已经按新的 chrome 算了——
    下一次哪怕只拖宽度，也会照着歪掉的基准把字号顶大一截。
    """
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
    ])
    window.show()
    app.processEvents()

    def drag(size: QSize) -> None:
        window._on_grip_drag_started(window.size())
        window._on_grip_dragged(size)
        window._on_grip_drag_finished()
        window._apply_scale()
        app.processEvents()

    drag(QSize(window.width(), window.height() + 60))  # 拖出一个手动尺寸
    assert window._manual_size is True
    frame = window.height()
    viewport = window.scroll.viewport().height()
    scale = window._scale
    font = window.scaled_config().stock_price_font_size

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()

    # 外框少掉标题栏那一条，内容区一个像素都不动。
    assert window.height() == pytest.approx(frame - window.title_bar.height(), abs=2)
    assert window.scroll.viewport().height() == pytest.approx(viewport, abs=2)
    assert window.scaled_config().stock_price_font_size == font

    # 原样开回来：外框回到原值，字号和缩放都不该被这一趟顶动。
    window.apply_config(Config(visible_rows=2))
    app.processEvents()
    assert window.height() == pytest.approx(frame, abs=2)
    assert window._scale == pytest.approx(scale)
    assert window.scaled_config().stock_price_font_size == font

    # 再开开关关也不能一次比一次跑偏：加回来的必须正好是减掉的那一条。
    settled = (window.height(), window.scroll.viewport().height())
    for _ in range(2):
        window.apply_config(Config(visible_rows=2, show_title_buttons=False))
        app.processEvents()
        hidden = (window.height(), window.scroll.viewport().height())
        window.apply_config(Config(visible_rows=2))
        app.processEvents()
        assert (window.height(), window.scroll.viewport().height()) == settled
    assert hidden[0] < settled[0]
    assert hidden[1] == settled[1]  # 内容区始终不变，少掉的只有 chrome

    # 藏起来之后只拖宽度、高度不动，缩放就该基本留在原地：修之前这里会照着
    # 少了一条 chrome 的基准算，scale 直接涨 25%，字号 19→24。
    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    drag(QSize(window.width() + 120, window.height()))
    assert window._scale == pytest.approx(scale, rel=0.08)
    assert abs(window.scaled_config().stock_price_font_size - font) <= 1
    window.close()


def test_context_menu_can_restore_title_buttons_without_a_tray(app):
    """系统托盘不是哪儿都有，右键菜单必须能把藏起来的按钮找回来。"""
    from PySide6.QtGui import QContextMenuEvent

    window = TickerWindow(Config(show_title_buttons=False))
    menu = window.build_menu()

    assert [action.text() for action in menu.actions() if action.text()] == [
        "立即刷新",
        "显示标题栏按钮",
        "设置…",
        "退出",
    ]
    toggle = next(a for a in menu.actions() if a.text() == "显示标题栏按钮")
    assert toggle.isCheckable() is True
    assert toggle.isChecked() is False  # 当前是藏起来的状态

    asked: list[bool] = []
    window.title_buttons_requested.connect(lambda: asked.append(True))
    toggle.trigger()
    assert asked == [True]

    # 行情文字上按右键也要能唤出菜单，而不是只有窗口空白处才行。
    popped: list[QPoint] = []
    window.popup_menu = popped.append
    app.sendEvent(
        window.empty_label,
        QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), QPoint(105, 105)),
    )
    assert popped == [QPoint(105, 105)]

    assert window.build_menu().actions()[0].text() == "立即刷新"
    window.close()


def test_hiding_title_bar_at_minimum_height_still_round_trips(app):
    """外框已经贴着最小高度时，减不动的那几像素不能在开回来时凭空多出来。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 80)
    app.processEvents()
    frame = window.height()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    assert window.height() == 56  # 夹到下限，只减掉了 24 而不是整条标题栏

    window.apply_config(Config(visible_rows=2))
    app.processEvents()
    assert window.height() == frame  # 加回来的也只有 24，不是 80 → 88
    window.close()


def test_font_size_change_while_hidden_restores_the_new_title_height(app):
    """藏着的时候改字号，标题栏会变高；开回来要按新高度补，不是旧的那条。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 220)
    app.processEvents()
    frame = window.height()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    removed = frame - window.height()
    hidden_frame = window.height()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False, font_size=22))
    app.processEvents()
    window.apply_config(Config(visible_rows=2, font_size=22))
    app.processEvents()

    restored = window.height() - hidden_frame
    assert restored > removed  # 字大了，标题栏也高了
    assert restored == pytest.approx(window.title_bar.height(), abs=2)
    window.close()


def test_drag_handle_offers_the_menu_when_click_through_hides_everything(app):
    """穿透 + 藏起按钮 + 没有托盘：左上角把手是最后一个能右键的地方。"""
    from PySide6.QtGui import QContextMenuEvent

    window = TickerWindow(Config(click_through=True, show_title_buttons=False))
    window.show()
    app.processEvents()
    assert window.handle.isVisible() is True

    popped: list[QPoint] = []
    window.popup_menu = popped.append
    app.sendEvent(
        window.handle,
        QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(3, 3), QPoint(77, 88)),
    )
    assert popped == [QPoint(77, 88)]
    window.close()


def test_hiding_title_bar_at_the_floor_removes_and_restores_nothing(app):
    """外框已经贴在下限时一点都减不掉，开回来也不能凭空长出一条标题栏。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 56)  # 配置里保存高度的下限
    app.processEvents()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    assert window.height() == 56  # 减不动

    window.apply_config(Config(visible_rows=2))
    app.processEvents()
    assert window.height() == 56  # 当初减掉 0，就该加回 0，而不是 56 → 88
    window.close()


def test_hiding_and_resizing_text_in_one_update_still_restores_correctly(app):
    """一次配置更新里既藏标题栏又改字号（WebUI 一次 POST 就能做到）。

    此刻 title_bar 的 sizeHint 已经是新字号的，和正在消失的那条没关系；
    把「减掉的高度」和新 hint 配成一对，开回来时比例换算就会当成没变过，
    只补回旧的那点高度，内容区平白被新的大标题栏吃掉一截。
    """
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("000001", "平安银行", 12.34, 12.00),
    ])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 220)
    app.processEvents()
    viewport = window.scroll.viewport().height()

    # 同一次更新：藏起标题栏 + 把字号从 13 调到 22
    window.apply_config(Config(visible_rows=2, show_title_buttons=False, font_size=22))
    app.processEvents()
    hidden_frame = window.height()
    assert window.scroll.viewport().height() == pytest.approx(viewport, abs=2)

    window.apply_config(Config(visible_rows=2, font_size=22))
    app.processEvents()

    # 补回来的要是「新字号下标题栏该占的高度」，内容区因此一点不变。
    assert window.height() - hidden_frame == pytest.approx(window.title_bar.height(), abs=2)
    assert window.scroll.viewport().height() == pytest.approx(viewport, abs=2)
    window.close()


def test_resizing_while_hidden_drops_the_clamped_bookkeeping(app):
    """藏着的时候用户重新拉过窗口，之前那次夹取过的记账就不作数了。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 80)
    app.processEvents()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    assert window._hidden_title_height > 0  # 贴着下限，只减掉了一部分

    # 藏着的时候把窗口拉高，空间够了，那份残缺的记账就该失效
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(QSize(window.width(), 200))
    window._on_grip_drag_finished()
    window._apply_scale()
    app.processEvents()
    # 判定方式是「外框还是不是减完时那个高度」，而不是逐个路径去清
    assert window._hidden_title_frame != window.height()
    grown, viewport = window.height(), window.scroll.viewport().height()

    window.apply_config(Config(visible_rows=2))
    app.processEvents()

    # 补的是标题栏此刻该占的整条高度，新选的内容区一点不被抠。
    assert window.height() - grown == pytest.approx(window.title_bar.height(), abs=2)
    assert window.scroll.viewport().height() == pytest.approx(viewport, abs=2)
    window.close()


def test_pressing_the_grip_without_dragging_keeps_the_bookkeeping(app):
    """只按一下右下角把手又松开，什么都没改，记账不该作废。"""
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 80)
    app.processEvents()
    frame = window.height()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    cached = window._hidden_title_height
    assert cached > 0

    # 按下把手、没拖动就松开：外框没变，记账要留着
    window._on_grip_drag_started(window.size())
    window._on_grip_drag_finished()
    app.processEvents()
    assert window._hidden_title_height == cached

    window.apply_config(Config(visible_rows=2))
    app.processEvents()
    assert window.height() == frame  # 补回当初减掉的 24，而不是整条 32
    window.close()


def test_chart_height_growth_while_hidden_invalidates_the_bookkeeping(app):
    """藏着的时候 K 线高度把外框撑高了，也算「外框被动过」。

    这条路径不经过右下角把手（走 _resize_to_grid 的 ensure_chart_height），
    所以记账的作废不能挂在拖拽上，只能按「外框还是不是减完时那个高度」判定。
    """
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 80)
    app.processEvents()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    assert 0 < window._hidden_title_height < window.title_bar.sizeHint().height()

    # K 线高度撑高外框，中途没有任何拖拽手势
    window.apply_config(
        Config(visible_rows=2, show_title_buttons=False, chart_height=160)
    )
    app.processEvents()
    grown, viewport = window.height(), window.scroll.viewport().height()
    assert grown > 80

    window.apply_config(Config(visible_rows=2, chart_height=160))
    app.processEvents()

    # 补的是标题栏整条高度，撑高后的内容区不被抠。
    assert window.height() - grown == pytest.approx(window.title_bar.height(), abs=2)
    assert window.scroll.viewport().height() == pytest.approx(viewport, abs=2)
    window.close()


def test_clamped_removal_plus_font_change_adds_the_title_growth(app):
    """减掉的高度被下限夹过，再改字号时只能加差额，不能按比例放大。

    夹过的记账是残值（80 的外框只减掉 24），乘比例等于把「没减成的那部分」
    也一起放大，标题栏就会从内容区里吃掉差额。
    """
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(visible_rows=2))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    window._manual_size = True
    window.resize(window.width(), 80)
    app.processEvents()

    window.apply_config(Config(visible_rows=2, show_title_buttons=False))
    app.processEvents()
    removed, hint_then = window._hidden_title_height, window._hidden_title_hint
    hidden = window.height()
    assert 0 < removed < hint_then  # 确实被下限夹过

    window.apply_config(Config(visible_rows=2, show_title_buttons=False, font_size=22))
    app.processEvents()
    window.apply_config(Config(visible_rows=2, font_size=22))
    app.processEvents()

    hint_now = window.title_bar.sizeHint().height()
    assert hint_now > hint_then  # 字号变大，标题栏也变高
    assert window.height() - hidden == removed + (hint_now - hint_then)
    window.close()


def test_tool_windows_stay_visible_when_the_app_is_inactive(app):
    """macOS 把 Qt.Tool 映射成 NSPanel，默认随应用失焦一起隐藏。

    盯盘组件几乎永远不是当前应用，不设这条属性在 macOS 上基本看不见。
    属性在别的平台是空操作，所以这里能在任何平台上验证它确实被设上了。
    """
    window = TickerWindow(Config())

    assert window.testAttribute(Qt.WA_MacAlwaysShowToolWindow) is True
    assert window.handle.testAttribute(Qt.WA_MacAlwaysShowToolWindow) is True

    # apply_config 会因为最前显示/鼠标穿透重设 windowFlags，属性不能跟着掉。
    window.apply_config(Config(always_on_top=False, click_through=True))
    assert window.windowFlags() & Qt.WindowTransparentForInput
    assert window.testAttribute(Qt.WA_MacAlwaysShowToolWindow) is True
    assert window.handle.testAttribute(Qt.WA_MacAlwaysShowToolWindow) is True
    window.close()



def test_resize_grip_is_overlay_and_tracks_bottom_right(app):
    window = TickerWindow(Config())
    window.resize(360, 220)
    window.show()
    app.processEvents()

    # grip 是窗口子控件，但不在 QVBoxLayout 里，因此不会再吃掉底部整行。
    assert window.grip.parent() is window
    assert window.layout().indexOf(window.grip) == -1
    assert not hasattr(window, "footer")
    assert window.grip.width() == 20
    assert window.grip.height() == 20
    assert window.grip.x() == window.width() - window.grip.width() - 2
    assert window.grip.y() == window.height() - window.grip.height() - 2

    window.grip.enterEvent(None)
    assert window.grip._hovered is True
    window.grip.leaveEvent(None)
    assert window.grip._hovered is False
    window.close()



def test_theme2_left_mirror_is_internal_and_keeps_the_frame_in_place(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", theme2_side="left", show_sparkline=False)
    window = TickerWindow(config)
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    window._sync_rows(
        [quote],
        {
            "600519": Trend(
                prices=[1275.0, 1290.0, 1304.66],
                high_price=1310.0,
                low_price=1268.0,
            )
        },
    )
    window.show()
    app.processEvents()
    geo = window.screen().availableGeometry()
    window.move(geo.left() + 20, geo.top() + 20)
    app.processEvents()

    row = window._rows["600519"]
    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.theme2_header)) == (0, 0, 1, 3)
    assert layout.getItemPosition(layout.indexOf(row.theme2_depth)) == (1, 0, 1, 3)

    before = window.geometry()
    watcher = _install_stub_watcher(window)
    row.theme2_depth.price_clicked.emit()
    app.processEvents()

    # 靠左停靠只体现在画布内部：价格列贴左、成交量轴被推到右外沿，外框不动。
    canvas = row.theme2_depth._layout()
    assert canvas.mirror is True
    assert canvas.price_left < row.theme2_depth.width() / 2
    assert canvas.volume_axis > canvas.price_right
    assert row.theme2_depth.expanded is True
    assert window.geometry() == before

    # 收起只认「点屏幕任意位置」，鼠标移开不再触发。
    app.sendEvent(row, QEvent(QEvent.Leave))
    app.processEvents()
    assert row.theme2_depth.expanded is True

    _click_away(window)
    app.processEvents()
    assert row.theme2_depth.expanded is False
    assert window.geometry() == before
    window.close()



def test_theme2_canvas_receives_mcp_bs_points(app, monkeypatch):
    from stockwidget import mcp_bs
    from stockwidget.providers.base import Quote

    requested = []

    def fake_bs_points(symbol, prices, now=None):
        requested.append((symbol, list(prices)))
        return [(1, "B"), (2, "S")]

    monkeypatch.setattr(mcp_bs, "bs_points", fake_bs_points)

    config = Config(display_theme="theme2", show_bs_points=True)
    row = QuoteRow("600519")
    row.apply_config(config)
    quote = Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)
    trend = Trend(
        prices=[1280.0, 1295.0, 1304.66],
        prev_close=1272.83,
        open_price=1280.0,
    )
    row.update_quote(quote, config, trend)

    # 主题2 的 K 线画在行内，B/S 仍要按当天 MCP 信号映射到分时索引上。
    assert requested == [("600519", trend.prices)]
    assert row.theme2_depth._signals == [(1, "B"), (2, "S")]
    assert row.theme2_depth._show_signals is True

    row.set_theme2_expanded(True, notify=False)
    assert row.theme2_depth._layout().has_chart_band is True
    row.close()


def test_theme2_bs_markers_are_cleared_when_the_quote_fails(app, monkeypatch):
    from stockwidget import mcp_bs
    from stockwidget.providers.base import Quote

    monkeypatch.setattr(mcp_bs, "bs_points", lambda symbol, prices, now=None: [(0, "B")])

    config = Config(display_theme="theme2", show_bs_points=True)
    row = QuoteRow("600519")
    row.apply_config(config)
    row.update_quote(
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        config,
        Trend(prices=[1300.0, 1304.66], prev_close=1272.83),
    )
    assert row.theme2_depth._signals == [(0, "B")]

    row.update_quote(Quote.failed("600519", "行情不可用"), config)
    assert row.theme2_depth._signals == []
    row.close()



def test_theme2_right_bottom_resize_preserves_manual_width_height_and_adapts_rows(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_title_buttons=False)
    window = TickerWindow(config)
    quotes = [
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        Quote.from_prices("300223", "北京君正", 381.66, 386.48),
    ]
    window._sync_rows(quotes)
    window.show()
    app.processEvents()

    requested = QSize(max(window.width() + 140, 360), max(window.height() + 150, 360))
    target = window._bounded_drag_size(requested)
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(requested)
    window._apply_scale()
    app.processEvents()

    dragged = QSize(window.size())
    assert window._manual_size is True
    assert dragged.width() == target.width()
    assert dragged.height() == target.height()

    # 模拟下一次行情刷新：手动框不能再被自然尺寸顶回去。
    window._sync_rows(quotes)
    app.processEvents()
    assert window.size() == dragged

    row_heights = [row.height() for row in window._rows.values()]
    assert max(row_heights) - min(row_heights) <= 2
    # 信息条固定在上，画布吃满剩下整行高度。
    for row in window._rows.values():
        margins = row.layout().contentsMargins()
        expected = (
            row.height()
            - row.theme2_header.height()
            - margins.top()
            - margins.bottom()
            - row.layout().verticalSpacing()
        )
        assert abs(row.theme2_depth.height() - expected) <= 1
    assert window.grip.x() == window.width() - window.grip.width() - 2
    assert window.grip.y() == window.height() - window.grip.height() - 2
    window.close()


def test_theme2_width_drag_gives_extra_room_to_the_chart_not_the_book(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", theme2_depth_width=84, show_title_buttons=False)
    window = TickerWindow(config)
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    app.processEvents()

    row = window._rows["600519"]
    before = row.theme2_depth._layout()
    start_height = window.height()
    requested = QSize(window.width() + 180, start_height)
    bounded = window._bounded_drag_size(requested)
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(requested)
    window._apply_scale()
    app.processEvents()
    after = row.theme2_depth._layout()

    assert window.width() == bounded.width()
    # 多出来的宽度归图表；盘口带只取「配置宽度 84」与「跨度 25%」里更长的一个，
    # 不会跟着窗口一起拉伸。
    canvas = row.theme2_depth
    assert after.kline_right - after.kline_left > before.kline_right - before.kline_left
    near, far = sorted((after.axis_x, after.depth_edge))
    span = abs(after.axis_x - 4.0)
    assert far - near == pytest.approx(max(84.0, span * DEPTH_SHARE_MIN), abs=1.0)
    # 盘口柱留在自己那一半里，不许被甩到对面。
    assert near >= canvas.width() / 2 - 1 or far <= canvas.width() / 2 + 1
    assert config.theme2_depth_width == 84
    assert window._config.theme2_depth_width == 84
    window.close()



def test_theme2_resize_does_not_scale_fonts(app):
    from stockwidget.providers.base import Quote

    config = Config(
        display_theme="theme2",
        font_size=13,
        stock_name_font_size=12,
        stock_price_font_size=15,
        stock_percent_font_size=11,
        dark_trade_font_size=10,
        chart_label_font_size=9,
        show_title_buttons=False,
    )
    window = TickerWindow(config)
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    app.processEvents()

    row = window._rows["600519"]
    before = (
        row.theme2_name_label.font().pixelSize(),
        row.theme2_code_label.font().pixelSize(),
        row.theme2_dark_label.font().pixelSize(),
        row.theme2_depth._config.stock_price_font_size,
        row.theme2_depth._config.stock_percent_font_size,
    )
    scale_before = window._scale

    requested = QSize(window.width() + 180, window.height() + 180)
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(requested)
    window._apply_scale()
    app.processEvents()

    after = (
        row.theme2_name_label.font().pixelSize(),
        row.theme2_code_label.font().pixelSize(),
        row.theme2_dark_label.font().pixelSize(),
        row.theme2_depth._config.stock_price_font_size,
        row.theme2_depth._config.stock_percent_font_size,
    )
    assert after == before
    assert window._scale == scale_before
    assert window.scaled_config() is window._config
    window.close()



def test_theme2_header_sits_above_the_canvas_in_two_tight_rows(app):
    from stockwidget.providers.base import Quote

    row = QuoteRow("600519")
    config = Config(display_theme="theme2")
    row.apply_config(config)
    row.resize(620, 150)
    row.update_quote(
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        config,
        Trend(
            prices=[1275.0, 1290.0, 1304.66],
            high_price=1310.0,
            low_price=1268.0,
        ),
    )
    row.set_theme2_expanded(True, notify=False)
    row.show()
    app.processEvents()

    header = row._theme2_header_layout
    margins = header.contentsMargins()
    # 左右各留 OUTER_PAD：画布里的竖直价格轴就压在离外沿 OUTER_PAD 的位置，
    # 于是轴线那一侧的信息栏文字与轴线严丝合缝（见
    # ``test_theme2_header_content_aligns_with_the_price_axis``）。
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
        OUTER_PAD,
        0,
        OUTER_PAD,
        0,
    )
    # 两行之间只留一个随字号缩放的细缝，不许摊成大段行距。
    assert header.spacing() == round(config.font_size * 0.15)
    assert header.spacing() < row.theme2_name_label.fontMetrics().height()
    assert header.count() == 2

    # 第一行「名字 + 代码 …… 暗盘」，第二行「高低」，两行都压在价格上方。
    first = row._theme2_first_layout
    assert first.count() == 4
    assert first.itemAt(0).widget() is row.theme2_name_label
    assert first.itemAt(1).widget() is row.theme2_code_label
    assert first.itemAt(3).widget() is row.theme2_dark_label
    assert row._theme2_second_layout.itemAt(1).widget() is row.theme2_high_low_label

    # y() 是相对各自父部件的：先统一映射到行的坐标系再比较。
    def top_in_row(widget) -> int:
        return widget.mapTo(row, QPoint(0, 0)).y()

    # 第一行三个字段各读各的字号，纵向居中后允许 1px 内的基线差。
    same_line = top_in_row(row.theme2_name_label)
    assert abs(top_in_row(row.theme2_code_label) - same_line) <= 2
    assert abs(top_in_row(row.theme2_dark_label) - same_line) <= 2
    assert top_in_row(row.theme2_high_low_label) > same_line
    assert top_in_row(row.theme2_header) < top_in_row(row.theme2_depth)
    row.close()


def test_theme2_header_content_aligns_with_the_price_axis(app):
    """信息栏在轴线那一侧的文字，边缘要与竖直价格轴对齐。

    用户口径（2026-09-23）：「上方股票名称……与轴线对齐」。价格轴现在贴行的外沿
    （价格列那条空白已经并给图表），所以靠左停靠时是**名称左端**落在轴上，靠右
    停靠时是**暗盘那一组的右端**落在轴上——两边共用同一份 OUTER_PAD 边距。
    """
    from stockwidget.providers.base import Quote

    for side in ("left", "right"):
        row = QuoteRow("600519")
        config = Config(display_theme="theme2", theme2_side=side)
        row.apply_config(config)
        row.resize(420, 150)
        row.update_quote(
            Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
            config,
            Trend(
                prices=[1275.0, 1290.0, 1304.66],
                high_price=1310.0,
                low_price=1268.0,
            ),
        )
        row.set_theme2_expanded(True, notify=False)
        row.show()
        app.processEvents()

        canvas = row.theme2_depth
        # 统一换算到「行」坐标系再比：直接 mapTo(canvas) 会拿到布局尚未激活时的
        # 陈旧位置（实测差 6px），而参与比较的两个数都取 row 坐标就没有这个坑。
        axis_in_row = canvas.mapTo(row, QPoint(0, 0)).x() + canvas._layout().axis_x
        if canvas._layout().mirror:
            edge = row.theme2_name_label.mapTo(row, QPoint(0, 0)).x()
        else:
            label = row.theme2_dark_label
            edge = label.mapTo(row, QPoint(0, 0)).x() + label.width()
        assert abs(edge - axis_in_row) <= 1, f"{side}: 信息栏边缘 {edge} 应落在价格轴 {axis_in_row} 上"
        row.close()


def test_theme2_collapsed_header_hides_name_code_and_high_low(app):
    """未点击时整条留空：名字 / 代码 / 高低价 / 暗盘统统点开后才显示。"""
    from stockwidget.providers.base import Quote

    row = QuoteRow("600519")
    config = Config(display_theme="theme2")
    row.apply_config(config)
    row.resize(420, 150)
    row.update_quote(
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        config,
        Trend(prices=[1275.0, 1290.0, 1304.66], high_price=1310.0, low_price=1268.0),
    )
    row.show()
    app.processEvents()

    assert row.theme2_name_label.isVisible() is False
    assert row.theme2_code_label.isVisible() is False
    assert row.theme2_high_low_label.isVisible() is False
    # 用户口径（2026-09-23）：「不点暗盘也不要显示」——暗盘也一起收起来。
    assert row.theme2_dark_label.isVisible() is False
    # 只是不画，文本仍留着，点开时不必等下一次行情刷新。
    assert row.theme2_name_label.full_text() == "贵州茅台"
    assert "600519" in row.theme2_code_label.full_text()
    assert "高 1310.00" in row.theme2_high_low_label.full_text()

    row.set_theme2_expanded(True, notify=False)
    app.processEvents()
    assert row.theme2_name_label.isVisible() is True
    assert row.theme2_code_label.isVisible() is True
    assert row.theme2_high_low_label.isVisible() is True
    assert row.theme2_dark_label.isVisible() is True

    row.set_theme2_expanded(False, notify=False)
    app.processEvents()
    assert row.theme2_name_label.isVisible() is False
    assert row.theme2_code_label.isVisible() is False
    assert row.theme2_high_low_label.isVisible() is False
    assert row.theme2_dark_label.isVisible() is False
    row.close()


def test_theme2_header_respects_the_settings_show_switches(app):
    """设置页里的显示开关在主题2 同样生效：关掉就不画。"""
    row = QuoteRow("600519")
    row.apply_config(
        Config(
            display_theme="theme2",
            show_stock_name=True,
            show_high_low=True,
            show_dark_trade=True,
        )
    )
    row.set_theme2_expanded(True, notify=False)
    row.show()
    app.processEvents()
    assert row.theme2_name_label.isVisible() is True
    assert row.theme2_code_label.isVisible() is True
    assert row.theme2_high_low_label.isVisible() is True
    assert row.theme2_dark_label.isVisible() is True

    row.apply_config(
        Config(
            display_theme="theme2",
            show_stock_name=False,
            show_high_low=False,
            show_dark_trade=False,
        )
    )
    app.processEvents()
    assert row.theme2_name_label.isVisible() is False
    assert row.theme2_code_label.isVisible() is False
    assert row.theme2_high_low_label.isVisible() is False
    assert row.theme2_dark_label.isVisible() is False
    # 收起态的「留白」规则不变：关掉开关只是不再画文字，行高照占。
    assert row.theme2_header.height() > 0
    row.close()


def test_theme2_high_low_is_neutral_in_both_directions(app):
    """主题2 的高低价格恒为中性次要灰，涨 / 跌两个方向画出来必须一模一样。

    用户口径（2026-09-23）：整行只有「股价 / 涨跌幅」跟涨跌色。改动前这里跟的是
    主题1 的走势图曲线色，于是一只上涨的股票连高低价都是红的。
    """
    from stockwidget.providers.base import Quote
    from stockwidget.ui.theme import MUTED

    expected = (
        f"color: rgba({MUTED.red()},{MUTED.green()},{MUTED.blue()},{MUTED.alpha()});"
    )
    for name, price, prev in (
        ("跌", 1304.66, 1400.00),
        ("涨", 1400.00, 1304.66),
    ):
        config = Config(display_theme="theme2")
        row = QuoteRow("600519")
        row.apply_config(config)
        quote = Quote.from_prices("600519", "贵州茅台", price, prev)
        row.update_quote(
            quote,
            config,
            Trend(prices=[prev, price], high_price=max(price, prev), low_price=min(price, prev)),
        )

        assert row.theme2_high_low_label.styleSheet() == expected, name
        row.close()


def test_theme2_code_follows_the_name_color(app):
    """代码与名字同色（用户选择），字号仍分主次。"""
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2")
    row = QuoteRow("600519")
    row.apply_config(config)
    row.update_quote(
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        config,
        Trend(prices=[1275.0, 1290.0, 1304.66], high_price=1310.0, low_price=1268.0),
    )

    assert row.theme2_code_label.styleSheet() == row.theme2_name_label.styleSheet()
    assert row.theme2_code_label.styleSheet() != "color: rgba(139,147,167,255);"
    row.close()


def test_theme2_show_stock_price_switch_reaches_the_canvas(app):
    row = QuoteRow("600519")
    row.apply_config(Config(display_theme="theme2", show_stock_price=False))
    assert row.theme2_depth._config.show_stock_price is False
    row.apply_config(Config(display_theme="theme2", show_stock_price=True))
    assert row.theme2_depth._config.show_stock_price is True
    row.close()


def test_theme2_collapsed_header_stays_hidden_across_quote_updates(app):
    """收起态收到行情刷新，不能把藏掉的四个标签又顶出来。"""
    from stockwidget.providers.base import Quote

    row = QuoteRow("600519")
    config = Config(display_theme="theme2")
    row.apply_config(config)
    row.resize(420, 150)
    for price in (1304.66, 1310.00):
        row.update_quote(
            Quote.from_prices("600519", "贵州茅台", price, 1272.83),
            config,
            Trend(prices=[1275.0, 1290.0, price], high_price=1310.0, low_price=1268.0),
        )
    row.show()
    app.processEvents()

    assert row.theme2_name_label.isVisible() is False
    assert row.theme2_code_label.isVisible() is False
    assert row.theme2_high_low_label.isVisible() is False
    assert row.theme2_dark_label.isVisible() is False
    row.close()


def test_theme2_header_height_does_not_change_on_expand(app):
    """展开只是多画几个字：信息条高度、画布高度、外框、兄弟行全都不动。

    注意暗盘标签的**横坐标**在展开时本来就会变（用户口径 2026-09-23：不点开就
    不显示，于是它是从「不画」变成「画在名字/代码之后」），所以这里守的是它出现
    前后仍在信息条第一行，而不是它的绝对位置。
    """
    from stockwidget.providers.base import Quote

    for side in ("right", "left"):
        config = Config(display_theme="theme2", theme2_side=side, show_title_buttons=False)
        window = TickerWindow(config)
        quotes = [
            Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
            Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        ]
        window._sync_rows(
            quotes,
            {
                "600519": Trend(
                    prices=[1275.0, 1290.0, 1304.66],
                    high_price=1310.0,
                    low_price=1268.0,
                )
            },
        )
        window.show()
        for _ in range(4):
            app.processEvents()

        row = window._rows["600519"]
        sibling = window._rows["603986"]
        frame_before = window.geometry()
        header_before = row.theme2_header.height()
        canvas_before = row.theme2_depth.geometry()
        dark_before = row.theme2_dark_label.mapTo(row, QPoint(0, 0))
        sibling_before = sibling.theme2_depth._layout()
        assert row.theme2_dark_label.isVisible() is False

        _install_stub_watcher(window)
        row.theme2_depth.price_clicked.emit()
        for _ in range(4):
            app.processEvents()

        assert row.theme2_header.height() == header_before
        assert row.theme2_depth.geometry() == canvas_before
        assert row.theme2_dark_label.isVisible() is True
        assert abs(row.theme2_dark_label.mapTo(row, QPoint(0, 0)).y() - dark_before.y()) <= 2
        assert window.geometry() == frame_before
        assert sibling.theme2_depth._layout() == sibling_before

        row.set_theme2_expanded(False, notify=False)
        for _ in range(4):
            app.processEvents()
        assert row.theme2_header.height() == header_before
        assert row.theme2_depth.geometry() == canvas_before
        assert row.theme2_dark_label.isVisible() is False
        window.close()


def test_theme2_header_elides_instead_of_widening_the_row(app):
    from stockwidget.providers.base import Quote

    row = QuoteRow("600519")
    config = Config(display_theme="theme2")
    row.apply_config(config)
    row.resize(168, 150)
    row.update_quote(
        Quote.from_prices("600519", "一只名字特别长的股票", 1304.66, 1272.83),
        config,
        Trend(prices=[1275.0, 1290.0, 1304.66], high_price=1310.0, low_price=1268.0),
    )
    app.processEvents()

    # QLabel 默认的最小宽度就是整段文字宽：一层层算下来会比外框还宽，而水平滚动
    # 条是关掉的，右侧内容会被直接裁掉。截断标签必须把最小宽度压到几个字符。
    assert row.minimumSizeHint().width() <= 200
    name = row.theme2_name_label
    assert name.minimumSizeHint().width() < name.sizeHint().width()
    assert name.full_text() == "一只名字特别长的股票"
    row.close()


def test_theme2_depth_is_not_intercepted_by_window_drag_filter(app):
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(display_theme="theme2"))
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    app.processEvents()

    depth = window._rows["600519"].theme2_depth
    start = window.frameGeometry().topLeft()
    point = depth.mapToGlobal(depth.rect().center())
    handled = window.eventFilter(
        depth,
        _WindowDragEvent(QEvent.MouseButtonPress, point, button=Qt.LeftButton),
    )

    assert handled is False
    assert window._move_drag_origin is None
    assert window.frameGeometry().topLeft() == start
    window.close()



def test_theme2_canvas_receives_minute_volume_and_latest_depth(app):
    from stockwidget.mcp_depth import DEPTH_FULL, DepthLevel, DepthSnapshot
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2")
    row = QuoteRow("600519")
    row.apply_config(config)
    trend = Trend(
        prices=[10.0, 10.1, 10.2],
        volumes=[100.0, 250.0, 180.0],
        prev_close=9.9,
        open_price=10.0,
    )
    row.update_quote(
        Quote.from_prices("600519", "贵州茅台", 10.2, 9.9),
        config,
        trend,
    )

    snapshot = DepthSnapshot(
        symbol="600519",
        levels=(
            DepthLevel("bid", 10.1, 100),
            DepthLevel("ask", 10.3, 120),
        ),
        full_depth=True,
        available=True,
        bid_count=1,
        ask_count=1,
        depth_mode=DEPTH_FULL,
    )
    row.update_depth(snapshot)

    canvas = row.theme2_depth
    assert list(canvas._trend.prices) == trend.prices
    assert list(canvas._trend.volumes) == trend.volumes
    assert canvas._prev_close == 9.9
    assert canvas._depth is snapshot

    row.set_theme2_expanded(True, notify=False)
    layout = canvas._layout()
    assert layout.has_chart_band is True
    # 成交量贴在盘口的另一侧：靠右停靠时盘口在右、成交量轴压左外沿，K 线居中。
    assert layout.volume_axis < layout.kline_left
    assert layout.kline_right <= layout.depth_edge
    row.close()



def test_theme2_expansion_never_resizes_the_frame_or_stirs_sibling_rows(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_title_buttons=False)
    window = TickerWindow(config)
    quotes = [
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
        Quote.from_prices("300223", "北京君正", 381.66, 386.48),
    ]
    window._sync_rows(quotes)
    window.show()
    app.processEvents()

    # 先手工拖宽：这是以前最容易被 sizeHint 缩回去的场景。
    requested = QSize(window.width() + 180, window.height())
    window._on_grip_drag_started(window.size())
    window._on_grip_dragged(requested)
    window._on_grip_drag_finished()
    app.processEvents()

    before_geometry = window.geometry()
    first = window._rows["600519"]
    sibling = window._rows["603986"]
    sibling_shape = sibling.theme2_depth._layout()
    first_shape = first.theme2_depth._layout()
    canvas_width = sibling.theme2_depth.width()

    watcher = _install_stub_watcher(window)
    first.theme2_depth.price_clicked.emit()
    app.processEvents()

    # 这条断言就是用户报的那个 bug：点一只股票，别的股票所在行不能跟着变。
    assert window.geometry() == before_geometry
    assert sibling.theme2_depth.width() == canvas_width
    assert sibling.theme2_depth._layout() == sibling_shape
    # 被点的那一行也不能跳：横向分区（含盘口柱长）点击前后必须逐字段相等。
    assert first.theme2_depth._layout() == first_shape
    # 点一只＝全列表展开：兄弟行跟被点行一起进入展开态（口径 2026-09-23）。
    assert first.theme2_depth.expanded is True
    assert sibling.theme2_depth.expanded is True
    assert first.theme2_depth.width() == canvas_width
    assert first.theme2_depth._layout().has_chart_band is True

    # 再刷新一次行情：外框与盘口形状都不能跳，展开态也不丢。
    window._sync_rows(quotes)
    app.processEvents()
    assert window.geometry() == before_geometry
    assert sibling.theme2_depth._layout() == sibling_shape
    assert first.theme2_depth._layout() == first_shape
    assert first.theme2_depth.expanded is True
    assert sibling.theme2_depth.expanded is True

    # 点屏幕任意位置 → 全部收起，外框不动。
    _click_away(window)
    app.processEvents()
    assert first.theme2_depth.expanded is False
    assert sibling.theme2_depth.expanded is False
    assert window.geometry() == before_geometry
    window.close()


def test_theme2_expansion_keeps_a_wide_manual_frame_exactly_as_it_is(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_title_buttons=False)
    window = TickerWindow(config)
    window._sync_rows([Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)])
    window.show()
    app.processEvents()

    # 宽到明显超过自然宽度：展开只重排画布内部，绝不能反过来缩小。
    window.resize(window.width() + 260, window.height())
    window._manual_size = True
    app.processEvents()
    before = window.geometry()
    row = window._rows["600519"]
    natural = row.theme2_depth.sizeHint().width()
    assert row.theme2_depth.width() > natural

    _install_stub_watcher(window)
    row.theme2_depth.price_clicked.emit()
    app.processEvents()

    assert window.geometry() == before
    assert row.theme2_depth.width() > natural
    assert row.theme2_depth._layout().has_chart_band is True
    window.close()



def test_theme2_header_fields_each_follow_their_own_font_settings(app):
    """主题2 顶栏不另起一套字号：名字/暗盘/高低各读设置页里对应的字段。

    ``theme2_popup_font_size`` 只留给「股票代码」和图上角标这类没有独立设置项的文字。
    """
    row = QuoteRow("600519")
    config = Config(
        display_theme="theme2",
        theme2_popup_font_size=18,
        stock_name_font_size=24,
        dark_trade_font_size=10,
        chart_label_font_size=8,
        font_size=13,
    )
    row.apply_config(config)

    assert row.theme2_name_label.font().pixelSize() == 24
    assert row.theme2_dark_label.font().pixelSize() == 10
    assert row.theme2_high_low_label.font().pixelSize() == 8
    assert row.theme2_code_label.font().pixelSize() == 18
    # 图表区的小字（「量」标注、分时/日K 角标、加载提示）吃主题2 自己的字号。
    assert row.theme2_depth._popup_font().pixelSize() == 18
    row.close()


def test_theme2_header_bold_follows_settings_instead_of_being_hardcoded(app):
    """加粗不再是写死的 True：取消勾选就得真的不加粗。"""
    row = QuoteRow("600519")
    row.apply_config(
        Config(
            display_theme="theme2",
            stock_name_bold=False,
            dark_trade_bold=False,
        )
    )
    assert row.theme2_name_label.font().bold() is False
    assert row.theme2_dark_label.font().bold() is False

    row.apply_config(
        Config(
            display_theme="theme2",
            stock_name_bold=True,
            dark_trade_bold=True,
        )
    )
    assert row.theme2_name_label.font().bold() is True
    assert row.theme2_dark_label.font().bold() is True
    row.close()


def test_theme2_price_click_only_signals_the_window(app):
    """行内不再自行展开：点价格只是发信号，展开态由窗口统一驱动。"""
    row = QuoteRow("600519")
    row.apply_config(Config(display_theme="theme2"))

    requested = []
    row.theme2_expand_requested.connect(requested.append)

    row.theme2_depth.price_clicked.emit()
    # 没有窗口接管时，行自己保持折叠。
    assert row._theme2_expanded is False
    assert requested == ["600519"]

    row.close()


def test_theme2_screen_click_on_a_price_area_does_not_collapse(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_title_buttons=False)
    window = TickerWindow(config)
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
    ])
    window.show()
    app.processEvents()

    watcher = _install_stub_watcher(window)
    window._rows["600519"].theme2_depth.price_clicked.emit()
    app.processEvents()
    assert window._theme2_all_expanded is True

    # 点在价格热区上的点击不算「点别处」：交给行内逻辑去切换，这里不收起。
    canvas = window._rows["600519"].theme2_depth
    rect = canvas._price_hit_rect()
    on_price = canvas.mapToGlobal(QPoint(round(rect.left()) + 1, canvas.height() // 2))
    window._on_screen_click(on_price)
    app.processEvents()
    assert window._theme2_all_expanded is True
    assert all(r.theme2_depth.expanded for r in window._rows.values())

    # 而已展开时再点一次价格（release 走行内信号）＝ 切回收起。
    canvas.price_clicked.emit()
    app.processEvents()
    assert window._theme2_all_expanded is False
    assert all(not r.theme2_depth.expanded for r in window._rows.values())
    window.close()


def test_theme2_switching_expanded_rows_keeps_every_row_sized(app):
    from stockwidget.providers.base import Quote

    config = Config(display_theme="theme2", show_title_buttons=False)
    window = TickerWindow(config)
    window._sync_rows([
        Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83),
        Quote.from_prices("603986", "兆易创新", 404.97, 432.37),
    ])
    window.show()
    app.processEvents()

    first = window._rows["600519"]
    second = window._rows["603986"]
    before_geometry = window.geometry()
    first_width = first.theme2_depth.width()
    second_width = second.theme2_depth.width()
    second_shape = second.theme2_depth._layout()

    _install_stub_watcher(window)
    first.theme2_depth.price_clicked.emit()
    app.processEvents()
    # 点一只＝全列表展开：两行的画布宽度全程不变。
    assert first.theme2_depth.expanded is True
    assert second.theme2_depth.expanded is True
    assert second.theme2_depth.width() == second_width
    assert second.theme2_depth._layout() == second_shape

    # 已展开时再点（另一只的）价格 ＝ 点任意位置，全部收起。
    second.theme2_depth.price_clicked.emit()
    app.processEvents()

    assert first.theme2_depth.expanded is False
    assert second.theme2_depth.expanded is False
    assert first.theme2_depth.width() == first_width
    assert second.theme2_depth.width() == second_width
    assert window.geometry() == before_geometry
    window.close()


def test_switching_theme2_back_to_classic_restores_stacked_and_hidden_titlebar(app):
    from stockwidget.providers.base import Quote

    window = TickerWindow(Config(display_theme="theme2", font_size=16))
    window._sync_rows(
        [Quote.from_prices("600519", "贵州茅台", 1304.66, 1272.83)]
    )
    window.show()
    app.processEvents()

    row = window._rows["600519"]
    assert row.theme2_header.isVisible() is True
    assert row.theme2_depth.isVisible() is True
    layout = row.layout()
    assert layout.getItemPosition(layout.indexOf(row.theme2_header)) == (0, 0, 1, 3)
    assert layout.getItemPosition(layout.indexOf(row.theme2_depth)) == (1, 0, 1, 3)
    # 主题2 的画布必须能被压扁（行数多/屏幕不够高时一路让位），不留最小行高。
    assert row.minimumHeight() == 0

    window.apply_config(
        Config(
            display_theme="theme1",
            row_style="stacked",
            show_title_buttons=False,
            font_size=13,
        )
    )
    app.processEvents()

    layout = row.layout()
    assert window.title_bar.isHidden() is True
    assert row.minimumHeight() == 0
    assert row.theme2_depth.isVisible() is False
    assert row.theme2_header.isVisible() is False
    # 主题2 的两个部件要彻底摘出网格，否则跨列格子项会和经典主题的格子重叠。
    assert layout.indexOf(row.theme2_depth) == -1
    assert layout.indexOf(row.theme2_header) == -1
    assert layout.getItemPosition(layout.indexOf(row.name_label))[:2] == (0, 0)
    assert layout.getItemPosition(layout.indexOf(row.price_label))[:2] == (0, 1)
    assert layout.getItemPosition(layout.indexOf(row.sparkline)) == (1, 0, 1, 2)
    assert layout.getItemPosition(layout.indexOf(row.dark_box))[:2] == (2, 0)
    assert layout.getItemPosition(layout.indexOf(row.percent_label))[:2] == (2, 1)
    window.close()
