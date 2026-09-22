from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.mcp_depth import DepthLevel, DepthSnapshot
from stockwidget.ui.depth_ladder import DepthLadder


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _is_green(pixel) -> bool:
    return pixel.green() > pixel.red() + 45 and pixel.green() > pixel.blue() + 25


def _is_red(pixel) -> bool:
    return pixel.red() > pixel.green() + 70 and pixel.red() > pixel.blue() + 40


def test_theme2_depth_uses_center_price_line_and_left_only_bars(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2", font_size=13))
    widget.set_quote(100.0, 1.25, QColor(240, 79, 90))
    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            available=True,
            levels=tuple(
                [DepthLevel("ask", 100.1 + index * 0.1, 120 + index * 50) for index in range(8)]
                + [DepthLevel("bid", 99.9 - index * 0.1, 140 + index * 45) for index in range(8)]
            ),
            ask_count=8,
            bid_count=8,
        )
    )
    image = widget.grab().toImage()
    center_y = image.height() // 2
    axis_x = image.width() - 5

    upper_green = 0
    upper_red = 0
    lower_green = 0
    lower_red = 0
    right_colored = 0

    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            green = _is_green(pixel)
            red = _is_red(pixel)
            if x > axis_x and (green or red):
                right_colored += 1
            if x >= axis_x - 2:
                continue
            if y < center_y - 10:
                upper_green += int(green)
                upper_red += int(red)
            elif y > center_y + 10:
                lower_green += int(green)
                lower_red += int(red)

    assert upper_green > 0, "当前价中线上方应有绿色卖盘"
    assert lower_red > 0, "当前价中线下方应有红色买盘"
    assert upper_red == 0, "卖盘区不应混入红色买盘"
    assert lower_green == 0, "买盘区不应混入绿色卖盘"
    assert right_colored == 0, "买卖挂单量条都必须位于价格轴左侧"


def test_theme2_uses_quote_price_as_vertical_center(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2"))
    widget.set_quote(100.0, 0.0, QColor(154, 163, 184))
    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            available=True,
            levels=(
                DepthLevel("ask", 100.2, 100),
                DepthLevel("ask", 100.4, 200),
                DepthLevel("bid", 99.8, 100),
                DepthLevel("bid", 99.6, 200),
            ),
            ask_count=2,
            bid_count=2,
        )
    )
    image = widget.grab().toImage()
    center = image.height() // 2

    green_rows = []
    red_rows = []
    for y in range(image.height()):
        for x in range(image.width() - 8):
            pixel = image.pixelColor(x, y)
            if _is_green(pixel):
                green_rows.append(y)
            if _is_red(pixel):
                red_rows.append(y)

    assert green_rows and max(green_rows) < center
    assert red_rows and min(red_rows) > center
