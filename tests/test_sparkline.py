"""走势图上的 B/S 竖线画法。

B 从底边向上画到曲线，S 从顶边向下画到曲线，两者都不穿过曲线。
用像素校验而不是看调用参数，因为「有没有越过曲线」只有画出来才算数。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
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
    from stockwidget.intraday import calculate_bs_points

    signals = calculate_bs_points(prices)
    assert signals, "构造的曲线应当能产生 B/S 点"

    widget = Sparkline()
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
    span = (high - low) or 0.01

    def y_of(value: float) -> float:
        return (image.height() - 2) - (value - low) / span * (image.height() - 4)

    def x_of(index: int) -> int:
        return round(index / (len(prices) - 1) * (image.width() - 1))

    return image, signals, prices, y_of, x_of


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
    from stockwidget.intraday import calculate_bs_points

    widget = Sparkline()
    widget.set_series(prices)
    widget.set_color(CURVE_COLOR)
    widget.set_annotations(
        None,
        calculate_bs_points(prices),
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
            received_at=1.0,
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
            received_at=1.0,
            full_depth=True,
            available=True,
            bid_count=2,
            ask_count=2,
        )
    )
    widget.resize(WIDTH, HEIGHT)
    image = widget.grab().toImage()

    side = round(widget._side_profile_width(image.width()))
    assert side == round(image.width() * 0.28)

    volume_pixels = []
    depth_pixels = []
    curve_pixels = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - TRADED_VOLUME_COLOR.red()) < TOLERANCE
                and abs(pixel.green() - TRADED_VOLUME_COLOR.green()) < TOLERANCE
                and abs(pixel.blue() - TRADED_VOLUME_COLOR.blue()) < TOLERANCE
            ):
                volume_pixels.append((x, y))
            if (
                abs(pixel.red() - DEPTH_BID_COLOR.red()) < TOLERANCE
                and abs(pixel.green() - DEPTH_BID_COLOR.green()) < TOLERANCE
                and abs(pixel.blue() - DEPTH_BID_COLOR.blue()) < TOLERANCE
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
    from stockwidget.intraday import calculate_bs_points

    prices = [10 + 0.9 * math.sin(i / 14) + 0.25 * math.sin(i / 3.3) for i in range(240)]
    signals = calculate_bs_points(prices)
    assert signals, "构造的曲线应当能产生 B/S 点"

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
            received_at=1.0,
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
