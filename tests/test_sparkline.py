"""走势图上的 B/S 竖线画法。

B 从底边向上画到曲线，S 从顶边向下画到曲线，两者都不穿过曲线。
用像素校验而不是看调用参数，因为「有没有越过曲线」只有画出来才算数。
"""

from __future__ import annotations

import math

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from stockwidget.ui.sparkline import BUY_COLOR, SELL_COLOR, Sparkline

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
