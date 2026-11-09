"""走势图上的 B/S 竖线画法。

B 从底边向上画到曲线，S 从顶边向下画到曲线，两者都不穿过曲线。
用像素校验而不是看调用参数，因为「有没有越过曲线」只有画出来才算数。
"""

from __future__ import annotations

import math
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
from stockwidget.kline import Bar
from stockwidget.ui.sparkline import (
    BUY_COLOR,
    DEPTH_BID_COLOR,
    SELL_COLOR,
    TRADED_VOLUME_COLOR,
    Sparkline,
)

# 曲线用绿色，避免和 B 红 / S 蓝混在一起，否则像素校验会把曲线当成竖线。
CURVE_COLOR = QColor(34, 197, 94)
WIDTH, HEIGHT = 560, 120
TOLERANCE = 70  # 抗锯齿后颜色会被稀释，比对留出余量


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def rendered(app):
    """画一段起落明显的曲线，返回图像与几何换算函数。"""
    prices = [10 + 0.9 * math.sin(i / 14) + 0.25 * math.sin(i / 3.3) for i in range(240)]

    signals = [(19, "B"), (37, "S"), (76, "B"), (88, "S")]

    widget = Sparkline()
    widget.set_fill_width(True)
    widget.set_series(prices)
    widget.set_prev_close(10.0)
    widget.set_color(CURVE_COLOR)
    widget.set_annotations(
        None,
        signals,
        show_signals=True,
        show_open_line=False,
        show_high_low=False,
        show_fill=False,
        grayscale=False,
    )
    widget.resize(WIDTH, HEIGHT)
    image = widget.grab().toImage()

    low = min(min(prices), 10.0)
    high = max(max(prices), 10.0)
    padding = (high - low) * 0.05
    low, high = low - padding, high + padding
    span = high - low

    def y_of(value: float) -> float:
        return (image.height() - 2) - (value - low) / span * (image.height() - 4)

    def x_of(index: int) -> int:
        return int(index / (len(prices) - 1) * (image.width() - 1))

    return image, signals, prices, y_of, x_of


def test_flat_and_one_tick_keep_historical_curve_at_same_height(app):
    widget = Sparkline()
    widget.set_fill_width(True)  # 本用例比较纵轴；用铺满模式在固定 X 位置取像素。
    widget.set_color(CURVE_COLOR)
    widget.set_annotations(None, [], show_signals=False, show_open_line=False,
                           show_high_low=False, show_fill=False, grayscale=False)
    widget.resize(WIDTH, HEIGHT)
    heights = []
    for high in [100.0, 100.01, 100.0, 100.01]:
        widget.set_series([100.0, 100.0, high])
        image = widget.grab().toImage()
        extent = _line_extent(image, 20, CURVE_COLOR)
        assert extent is not None
        heights.append(extent)
    assert len(set(heights)) == 1


@pytest.mark.parametrize("card", [False, True])
@pytest.mark.parametrize("recent", [False, True])
def test_price_window_fits_candles_independently_of_book_and_reference_prices(app, monkeypatch, card, recent):
    widget = Sparkline()
    widget.resize(500, 240)
    widget.set_card_style(card)
    widget.set_display_options(show_tdw=False)
    widget.set_series([100, 101], realtime_bars=[
        Bar("2026-11-06 09:30:01", 100, 102, 99, 101, 100),
    ], recent=recent)
    widget.set_prev_close(60)
    widget.set_annotations(180, [], show_signals=False, show_open_line=True,
                           show_high_low=False, show_fill=False, grayscale=False)
    mappings = []

    def capture(_painter, _width, height, y_of, _book_width):
        mappings.append((height, y_of(99), y_of(102)))

    monkeypatch.setattr(widget, "_draw_depth", capture)
    widget.grab()
    widget.set_depth(DepthSnapshot("600000", available=True, levels=(
        DepthLevel("bid", 1, 1000), DepthLevel("ask", 500, 1000),
    )))
    widget.set_prev_close(200)
    widget.set_annotations(20, [], show_signals=False, show_open_line=True,
                           show_high_low=False, show_fill=False, grayscale=False)
    widget.grab()
    assert len(mappings) == 2
    assert mappings[0] == pytest.approx(mappings[1])
    height, low_y, high_y = mappings[0]
    assert 0 < high_y < height * .1
    assert height * .9 < low_y < height


@pytest.mark.parametrize("card", [False, True])
def test_outside_reference_lines_do_not_expand_chart_or_leak_into_tdw(app, card):
    widget = Sparkline()
    widget.resize(500, 240)
    widget.set_card_style(card)
    widget.set_series([100, 101, 102])
    widget.set_annotations(None, [], show_signals=True, show_open_line=True,
                           show_high_low=False, show_fill=False, grayscale=False)
    widget.set_tdw_power([], show_levels=True)
    baseline = widget.grab().toImage()
    widget.set_prev_close(99.5)
    widget.set_annotations(99.4, [], show_signals=True, show_open_line=True,
                           show_high_low=False, show_fill=False, grayscale=False)
    assert widget.grab().toImage() == baseline
    widget.set_prev_close(100.5)
    assert widget.grab().toImage() != baseline


@pytest.mark.parametrize("card", [False, True])
@pytest.mark.parametrize("size", [(86, 64), (500, 240)])
def test_high_low_labels_remain_in_opposite_corners_with_book_and_tdw(app, monkeypatch, card, size):
    from PySide6.QtGui import QPainter

    widget = Sparkline()
    widget.resize(*size)
    widget.set_card_style(card)
    widget.set_series([442, 443], realtime_bars=[
        Bar("2026-11-06 09:30:01", 442, 444.38, 440.27, 443, 100),
    ])
    widget.set_depth(DepthSnapshot("603986", available=True, full_depth=True, levels=(
        DepthLevel("bid", 300, 200), DepthLevel("ask", 500, 100),
    )))
    widget.set_tdw_power([.5, 3.5], show_levels=True)
    widget.set_annotations(None, [], show_signals=True, show_open_line=False,
                           show_high_low=True, show_fill=False, grayscale=False)
    labels = []
    original = QPainter.drawText

    def record(self, *args):
        if args[-1] in {"高 444.38", "低 440.27"}:
            labels.append(args)
        return original(self, *args)

    monkeypatch.setattr(QPainter, "drawText", record)
    widget.grab()
    high, low = labels[-2:]
    assert high[-1] == "高 444.38" and low[-1] == "低 440.27"
    assert high[0].x() == 2
    assert low[0].x() > high[0].x()
    assert 0 < high[0].y() < low[0].y() < size[1]
    labels.clear()
    widget.set_annotation_options(show_signals=True, show_open_line=False,
                                  show_high_low=False, show_fill=False, grayscale=False)
    widget.grab()
    assert not labels


def test_first_candle_already_has_high_low_labels(app, monkeypatch):
    from PySide6.QtGui import QPainter

    widget = Sparkline()
    widget.resize(300, 150)
    widget.set_series([441], realtime_bars=[Bar("2026-11-06 09:30:01", 440, 442, 439, 441, 100)])
    labels = []
    monkeypatch.setattr(QPainter, "drawText", lambda self, *args: labels.append(args[-1]))
    widget.grab()
    assert "高 442.00" in labels and "低 439.00" in labels


def _line_extent(image, x: int, color: QColor):
    """某一列上属于该颜色的像素跨度。"""
    rows = [
        y
        for y in range(image.height())
        if abs(image.pixelColor(x, y).red() - color.red()) < TOLERANCE
        and abs(image.pixelColor(x, y).green() - color.green()) < TOLERANCE
        and abs(image.pixelColor(x, y).blue() - color.blue()) < TOLERANCE
    ]
    return (min(rows), max(rows)) if rows else None


@pytest.mark.parametrize('side', ['B', 'S'])
def test_base_signal_has_same_direction_and_color_as_confirmed(app, side):
    widget = Sparkline()
    widget.set_series([10., 11., 10.5])
    widget.resize(WIDTH, HEIGHT)
    images = []
    for signal in (side, side + '1'):
        widget.set_annotations(None, [(1, signal)], show_signals=True,
            show_open_line=False, show_high_low=False, show_fill=False, grayscale=False)
        images.append(widget.grab().toImage())
    assert images[0] == images[1]


def test_buy_lines_rise_from_bottom_and_stop_at_curve(rendered):
    image, signals, prices, y_of, x_of = rendered
    checked = 0
    for index, kind in signals:
        if kind != "B":
            continue
        extent = _line_extent(image, x_of(index), BUY_COLOR)
        if extent is None:
            continue
        top, bottom = extent
        assert bottom >= image.height() - 3, f"B@{index} 没有画到底边: {bottom}"
        assert top >= y_of(prices[index]) - 3, f"B@{index} 越过了曲线: {top} < {y_of(prices[index]):.0f}"
        checked += 1
    assert checked, "至少应校验到一条 B 线"


def test_sell_lines_drop_from_top_and_stop_at_curve(rendered):
    image, signals, prices, y_of, x_of = rendered
    checked = 0
    for index, kind in signals:
        if kind != "S":
            continue
        extent = _line_extent(image, x_of(index), SELL_COLOR)
        if extent is None:
            continue
        top, bottom = extent
        assert top <= 3, f"S@{index} 没有从顶边开始: {top}"
        assert bottom <= y_of(prices[index]) + 3, f"S@{index} 越过了曲线: {bottom} > {y_of(prices[index]):.0f}"
        checked += 1
    assert checked, "至少应校验到一条 S 线"


def test_signals_hidden_when_switched_off(app):
    prices = [10 + 0.9 * math.sin(i / 14) for i in range(120)]

    widget = Sparkline()
    widget.set_series(prices)
    widget.set_color(CURVE_COLOR)
    widget.set_annotations(
        None,
        [(12, "B"), (36, "S")],
        show_signals=False,
        show_open_line=False,
        show_high_low=False,
        show_fill=False,
        grayscale=False,
    )
    widget.resize(WIDTH, HEIGHT)
    image = widget.grab().toImage()

    blues = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if abs(image.pixelColor(x, y).blue() - SELL_COLOR.blue()) < 40
        and image.pixelColor(x, y).red() < 120
    )
    assert blues == 0, "关掉开关后不应画出 S 蓝线"


def test_thousand_depth_is_drawn_inside_the_chart_right_edge(app):
    prices = [9.7 + index * 0.006 for index in range(120)]
    levels = tuple(
        [
            DepthLevel("bid", 9.78 + index * 0.03, 100 + index * 80)
            for index in range(6)
        ]
        + [
            DepthLevel("ask", 10.02 + index * 0.03, 120 + index * 70)
            for index in range(6)
        ]
    )
    widget = Sparkline()
    widget.set_series(prices)
    widget.set_color(CURVE_COLOR)
    widget.set_annotations(
        None,
        [],
        show_signals=False,
        show_open_line=False,
        show_high_low=False,
        show_fill=False,
        grayscale=False,
    )
    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=levels,
            received_at=time.time(),
            full_depth=True,
            available=True,
            bid_count=6,
            ask_count=6,
        )
    )
    widget.resize(WIDTH, HEIGHT)
    image = widget.grab().toImage()

    red_pixels = 0
    for y in range(image.height()):
        for x in range(round(image.width() * 0.70), image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - DEPTH_BID_COLOR.red()) < TOLERANCE
                and abs(pixel.green() - DEPTH_BID_COLOR.green()) < TOLERANCE
                and abs(pixel.blue() - DEPTH_BID_COLOR.blue()) < TOLERANCE
            ):
                red_pixels += 1
    assert red_pixels > 0, "千档买盘应在 K 线内部右侧画出深度条"



def test_theme2_traded_volume_is_opposite_depth_with_equal_sidebar_width(app):
    prices = [9.80, 9.90, 10.00, 10.10, 10.20]
    volumes = [100, 250, 500, 200, 120]
    levels = (
        DepthLevel("bid", 9.90, 200),
        DepthLevel("bid", 9.80, 500),
        DepthLevel("ask", 10.10, 220),
        DepthLevel("ask", 10.20, 450),
    )

    curve_color = QColor(180, 90, 220)
    widget = Sparkline()
    widget.set_series(prices)
    widget.set_volume_profile(volumes, enabled=True)
    widget.set_color(curve_color)
    widget.set_annotations(
        None,
        [],
        show_signals=False,
        show_open_line=False,
        show_high_low=False,
        show_fill=False,
        grayscale=False,
    )
    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=levels,
            received_at=time.time(),
            full_depth=True,
            available=True,
            bid_count=2,
            ask_count=2,
        )
    )
    widget.resize(WIDTH, HEIGHT)
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    widget.render(image, QPoint(), QRegion(), QWidget.DrawChildren)

    side = round(widget._side_profile_width(image.width()))
    assert side == round(image.width() * 0.28)

    volume_pixels = []
    depth_pixels = []
    curve_pixels = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - TRADED_VOLUME_COLOR.red()) < 6
                and abs(pixel.green() - TRADED_VOLUME_COLOR.green()) < 6
                and abs(pixel.blue() - TRADED_VOLUME_COLOR.blue()) < 6
            ):
                volume_pixels.append((x, y))
            if (
                abs(pixel.red() - DEPTH_BID_COLOR.red()) < 6
                and abs(pixel.green() - DEPTH_BID_COLOR.green()) < 6
                and abs(pixel.blue() - DEPTH_BID_COLOR.blue()) < 6
            ):
                depth_pixels.append((x, y))
            if (
                abs(pixel.red() - curve_color.red()) < 45
                and abs(pixel.green() - curve_color.green()) < 45
                and abs(pixel.blue() - curve_color.blue()) < 45
            ):
                curve_pixels.append((x, y))

    assert volume_pixels, "左侧应画出已成交量价格分布"
    assert depth_pixels, "右侧应画出实时挂单分布"
    assert max(x for x, _ in volume_pixels) <= side + 2
    assert min(x for x, _ in depth_pixels) >= image.width() - side - 3

    # K线只占中间区域，不再与左右成交量/挂单侧栏重叠。
    assert curve_pixels
    assert min(x for x, _ in curve_pixels) >= side
    assert max(x for x, _ in curve_pixels) <= image.width() - side


def test_volume_profile_does_not_create_bottom_volume_panel(app):
    widget = Sparkline()
    widget.set_series([10.0, 10.1, 10.2, 10.15])
    widget.set_volume_profile([100, 200, 300, 150], enabled=True)
    widget.set_annotations(
        None,
        [],
        show_signals=False,
        show_open_line=False,
        show_high_low=False,
        show_fill=False,
        grayscale=False,
    )
    widget.resize(WIDTH, HEIGHT)

    # 成交量是按价格 Y 轴分布的左侧横条，不占用额外底部高度。
    assert widget.height() == HEIGHT
    assert widget._show_volume_profile is True
    assert len(widget._volumes) == len(widget._series)


# ---------------------------------------------------------------- 灰度显示

# 深底铺在控件下面：走势图自己不带背景，用 Qt 调色板底色会冲淡半透明元素。
BACKDROP = QColor(24, 27, 34)
# 底色自身的通道差就有 10（24/27/34），所以「是否还有彩色」的阈值必须高于它。
CHROMA_TOLERANCE = 14


def _sparkline_widget(level: int, *, grayscale: bool = True) -> Sparkline:
    """把走势图上所有**带颜色**的元素一次点亮：曲线 / 量价分布 / 千档买卖 / B·S / 开盘线。

    只要有一个站点漏了灰度转换，图里就会留下彩色像素。
    """

    prices = [10 + 0.9 * math.sin(i / 14) + 0.25 * math.sin(i / 3.3) for i in range(240)]
    signals = [(12, "B"), (36, "S")]

    levels = tuple(
        [DepthLevel("bid", 9.78 + index * 0.03, 100 + index * 80) for index in range(6)]
        + [DepthLevel("ask", 10.02 + index * 0.03, 120 + index * 70) for index in range(6)]
    )

    widget = Sparkline()
    widget.set_series(prices)
    widget.set_volume_profile([100 + (index % 17) * 30 for index in range(len(prices))], enabled=True)
    widget.set_prev_close(10.0)
    widget.set_color(CURVE_COLOR)  # 故意给曲线一个绿色：灰度模式必须吃掉它
    widget.set_annotations(
        10.0,
        signals,
        show_signals=True,
        show_open_line=True,
        show_high_low=True,
        show_fill=True,
        grayscale=grayscale,
        grayscale_level=level,
    )
    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=levels,
            received_at=time.time(),
            full_depth=True,
            available=True,
            bid_count=6,
            ask_count=6,
        )
    )
    widget.resize(WIDTH, HEIGHT)
    return widget


def _render_on_backdrop(widget: Sparkline) -> QImage:
    """先铺深底再以 DrawChildren 渲染（默认 flags 会盖一层 Qt 调色板浅灰）。"""
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(BACKDROP)
    widget.render(image, QPoint(0, 0), QRegion(), QWidget.DrawChildren)
    return image


def _pixel_counts(image: QImage) -> tuple[int, float]:
    """返回 (彩色像素数, 全图平均亮度)。"""
    colored = 0
    total = 0.0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            rgb = (pixel.red(), pixel.green(), pixel.blue())
            if max(rgb) - min(rgb) > CHROMA_TOLERANCE:
                colored += 1
            total += sum(rgb) / 3.0
    return colored, total / max(1, image.width() * image.height())


def _pixels_near(image: QImage, level: int, tolerance: int = 6) -> int:
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if all(abs(channel - level) <= tolerance for channel in image.pixelColor(x, y).getRgb()[:3])
    )


def test_grayscale_collapses_every_colored_element_into_one_configurable_gray(app):
    """灰度显示时不再按原色分档：整张图只剩配置的那一个灰阶。"""
    brightness = {}
    for level in (90, 230):
        image = _render_on_backdrop(_sparkline_widget(level))
        colored, mean = _pixel_counts(image)
        assert colored == 0, f"灰阶 {level} 下仍有彩色像素"
        # 曲线是满不透明画的，所以图里应当能找到配置的那个灰阶本身。
        assert _pixels_near(image, level) > 20, f"没画出灰阶 {level}"
        brightness[level] = mean

    # 灰阶值真的参与绘制：调暗了整张图也该更暗。
    assert brightness[90] < brightness[230]


def test_colored_elements_still_show_up_while_grayscale_is_off(app):
    """反证：同样的元素在非灰度下确实带彩色，上面的断言才不是空转。"""
    colored, _mean = _pixel_counts(_render_on_backdrop(_sparkline_widget(150, grayscale=False)))
    assert colored > 0


def test_tdw_markers_stay_on_the_price_scale_with_grayscale_l2(app, monkeypatch):
    from stockwidget.ui import tdw_overlay

    w = Sparkline()
    w.resize(300, 100)
    w.set_series([10., 11., 12.])
    w.set_tdw_power([.4, .6, .8])
    w.set_annotations(None, [(1, "B2")], show_signals=True, show_open_line=False,
                      show_high_low=False, show_fill=False, grayscale=True)
    w.set_depth(DepthSnapshot("600000", levels=(DepthLevel("bid", 11.1, 50),), available=True))
    positions = []
    monkeypatch.setattr(tdw_overlay, "draw_event", lambda painter, x, y, *args: positions.append(y))
    for enabled in (False, True):
        w.set_show_depth(enabled)
        w.grab()
    # 下方 33px 属于动力线，价格中点在上方主图中央。
    assert positions == pytest.approx([33.5, 33.5], abs=1.)


@pytest.mark.parametrize("height", [8, 20, 96])
def test_four_tdw_guides_survive_compact_warmup_and_stay_out_of_book(app, height):
    w = Sparkline()
    w.setFixedSize(360, height)
    w.set_series([11., 11.], ["10:01:00", "10:01:03"])
    w.set_annotations(None, [], show_signals=True, show_open_line=False,
                      show_high_low=False, show_fill=False, grayscale=True)
    w.set_show_depth(True)
    w.set_depth(DepthSnapshot("600000", levels=(DepthLevel("bid", 11., 50),), available=True))
    # A new session has prices but too few real-time bars for a TDW value.
    w.set_tdw_power([], show_levels=False)
    before = _render_on_backdrop(w)
    w.set_tdw_power([], show_levels=True)
    after = _render_on_backdrop(w)
    rows = [y for y in range(height)
            if sum(before.pixel(x, y) != after.pixel(x, y) for x in range(120, 220)) > 25]
    assert len(rows) == 4
    if height == 96:
        assert rows == [70, 72, 90, 93]
    # 高卡的盘口随主图一起缩短，下方的参考线不能进入盘口列。
    if height == 96:
        assert all(after.pixelColor(x, y) == BACKDROP
                   for x in range(260, 360) for y in range(64, height))
    else:
        assert all(before.pixel(x, y) == after.pixel(x, y)
                   for x in range(260, 360) for y in range(height))
    w.close()


def test_dense_tdw_paths_keep_real_endpoint_and_break_at_missing_data(app):
    from stockwidget.ui.tdw_overlay import series_path, visible_events

    values = [i % 4 for i in range(2000)] + [None] + [3, 2, 1]
    path = series_path(values, lambda i: i / 10, float, 4)
    coords = [path.elementAt(i) for i in range(path.elementCount())]
    assert len(coords) < 60
    assert (coords[0].x, coords[0].y) == (0, values[0])
    assert (coords[-1].x, coords[-1].y) == (200.3, values[-1])
    assert any(el.isMoveTo() and el.x == 200.1 for el in coords)
    assert any(el.x == 199.9 and el.y == values[1999] for el in coords)

    signals = [(n, "B1" if n % 2 else "S2") for n in range(2000)]
    visible = list(visible_events(signals, len(values), lambda i: i / 10))
    assert visible[-1] == signals[-1] and len(visible) < 20
    assert all(right[0] - left[0] >= 120 for left, right in zip(visible, visible[1:]))
    assert len(signals) == 2000


def test_classic_full_book_and_tdw_markers_have_no_text(app, monkeypatch):
    from PySide6.QtGui import QPainter
    from stockwidget.mcp_depth import DEPTH_FULL

    widget = Sparkline()
    widget.resize(360, 120)
    widget.set_fill_width(True)
    widget.set_series([10., 11., 12.])
    widget.set_tdw_power([0.2, 1.5, 3.5])
    widget.set_annotations(None, [(0, "B1"), (2, "S2*")], show_signals=True,
                           show_open_line=False, show_high_low=False, show_fill=False, grayscale=True)
    levels = tuple(DepthLevel("bid" if i < 100 else "ask", 10 + i / 100, i + 1)
                   for i in range(200))
    widget.set_depth(DepthSnapshot("600000", levels=levels, available=True, depth_mode=DEPTH_FULL))
    captions = []
    monkeypatch.setattr(QPainter, "drawText", lambda self, *args: captions.append(args[-1]))
    widget.grab()
    assert captions == []


def test_card_has_no_time_tick_row_in_session_or_recent_view(app, monkeypatch):
    from PySide6.QtGui import QPainter

    chart = Sparkline()
    chart.set_card_style(True)
    chart.resize(320, 160)
    times = ["13:41:00", "13:46:00"]
    chart.set_series([10., 11.], times)
    captions = []
    monkeypatch.setattr(QPainter, "drawText", lambda self, *args: captions.append(args[-1]))
    chart.grab()
    assert not {"09:30", "10:30", "11:30/13:00", "14:00", "15:00"} & set(captions)
    captions.clear()
    chart.set_series([10., 11.], times, recent=True)
    chart.grab()
    assert not set(times) & set(captions)
    assert "15:00" not in captions


@pytest.mark.parametrize('size', [(100, 80), (400, 240), (900, 400)])
@pytest.mark.parametrize('configured_width', [0, 30, 1800])
def test_theme3_depth_strokes_never_exceed_half_candle_plot(app, monkeypatch, size, configured_width):
    from PySide6.QtGui import QPainter
    from stockwidget.ui import tdw_overlay
    chart = Sparkline()
    chart.resize(*size)
    chart.set_card_style(True)
    chart.set_display_options(show_tdw=False, depth_width=configured_width, depth_line_width=.7)
    chart.set_series([10, 10.1], realtime_bars=[Bar('10:00:00', 10, 10.2, 9.9, 10.1, 100)])
    chart.set_annotations(None, [], show_signals=False, show_open_line=False,
                          show_high_low=False, show_fill=False, grayscale=False)
    chart.set_depth(DepthSnapshot('603986', available=True, levels=(
        DepthLevel('bid', 10, 100), DepthLevel('ask', 10.1, 200),
    )))
    widths, plot_spans = [], []
    original_draw = QPainter.drawLine
    def draw(painter, *args):
        if len(args) == 2 and painter.pen().widthF() == pytest.approx(.7):
            widths.append(abs(args[1].x() - args[0].x()))
        return original_draw(painter, *args)
    # Capture the plot from its curve; card candles use a separate draw path.
    original_path = tdw_overlay.series_path
    def path(points, x_of, *args):
        plot_spans.append(x_of(len(points) - 1) - x_of(0))
        return original_path(points, x_of, *args)
    monkeypatch.setattr(QPainter, 'drawLine', draw)
    monkeypatch.setattr(tdw_overlay, 'series_path', path)
    chart.set_fill_width(True)
    chart.grab()
    assert widths and plot_spans
    assert max(widths) <= plot_spans[0] * .5 + 1e-6
    if configured_width:
        assert max(widths) <= configured_width
    chart.close()


def test_old_depth_is_hidden_and_source_time_is_shown_even_after_fresh_receipt(app, monkeypatch):
    from datetime import datetime
    from PySide6.QtGui import QPainter
    import stockwidget.mcp_depth as depth
    now = datetime.fromisoformat('2026-11-09T10:35:00+08:00').timestamp()
    monkeypatch.setattr(depth.time, 'time', lambda: now)
    chart = Sparkline()
    chart.resize(500, 300)
    chart.set_card_style(True)
    chart.set_series([10., 11.], ['10:34', '10:35'])
    chart.set_display_options(depth_line_width=.7)
    chart.set_depth(DepthSnapshot('603986', available=True, received_at=now,
        source_at='2026-11-09T10:00:00+08:00', levels=(DepthLevel('bid', 10, 100),)))
    texts, bars = [], []
    original_text, original_line = QPainter.drawText, QPainter.drawLine
    def text(painter, *args):
        texts.append(str(args[-1]))
        return original_text(painter, *args)
    def line(painter, *args):
        if painter.pen().widthF() == pytest.approx(.7):
            bars.append(args)
        return original_line(painter, *args)
    monkeypatch.setattr(QPainter, 'drawText', text)
    monkeypatch.setattr(QPainter, 'drawLine', line)
    chart.grab()
    assert not bars
    assert 'L2 过期·等待新数据' in texts
    assert '盘口采样 10:00:00' in texts
    assert not any('采样 10:35:00' in text for text in texts)
    chart.close()


def test_depth_age_timer_invalidates_a_frozen_frame_without_quote_updates(app, monkeypatch):
    from datetime import datetime
    from PySide6.QtTest import QTest
    import stockwidget.mcp_depth as depth
    now = [datetime.fromisoformat('2026-11-09T10:35:00+08:00').timestamp()]
    monkeypatch.setattr(depth.time, 'time', lambda: now[0])
    chart = Sparkline()
    chart.set_depth(DepthSnapshot('603986', available=True, received_at=now[0],
                                  levels=(DepthLevel('bid', 10, 100),)))
    chart._depth_clock.setInterval(10)
    assert not chart._depth_expired
    now[0] += 31
    QTest.qWait(30)
    assert chart._depth_expired
    chart.set_depth(None)
    assert not chart._depth_clock.isActive()
    chart.close()
