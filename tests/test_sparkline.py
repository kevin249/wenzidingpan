"""走势图上的 B/S 竖线画法。

B 从底边向上画到曲线，S 从顶边向下画到曲线，两者都不穿过曲线。
用像素校验而不是看调用参数，因为「有没有越过曲线」只有画出来才算数。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

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
