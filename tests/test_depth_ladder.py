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


def test_theme2_right_dock_places_price_left_and_depth_only_on_right(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2", theme2_side="right", font_size=13))
    widget.set_quote(100.0, 1.25, QColor(154, 163, 184))
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
    mirror, axis_x, depth_inner_x, price_left, price_right = widget._horizontal_geometry()
    center_y = image.height() // 2

    assert mirror is False
    assert axis_x == pytest.approx(image.width() - 5)
    assert price_left < price_right < depth_inner_x < axis_x
    assert widget._price_rect.center().x() < image.width() * 0.35
    assert widget._price_rect.top() < center_y < widget._price_rect.bottom()

    upper_green = 0
    lower_red = 0
    colored_inside_price_side = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            green = _is_green(pixel)
            red = _is_red(pixel)
            if x < round(depth_inner_x) - 1 and (green or red):
                colored_inside_price_side += 1
            if x >= round(depth_inner_x):
                if y < center_y - 10:
                    upper_green += int(green)
                elif y > center_y + 10:
                    lower_red += int(red)

    assert colored_inside_price_side == 0, "挂单不能覆盖最左侧股价/虚线区域"
    assert upper_green > 0, "卖盘应在右侧盘口上半区"
    assert lower_red > 0, "买盘应在右侧盘口下半区"

    guide_start = round(price_right + 3)
    guide_end = round(axis_x - 3)
    guide_colors = [image.pixelColor(x, center_y) for x in range(guide_start, guide_end)]
    guide_hits = sum(
        1
        for pixel in guide_colors
        if pixel.alpha() > 40
        and abs(pixel.red() - pixel.green()) < 24
        and abs(pixel.green() - pixel.blue()) < 30
    )
    guide_gaps = sum(1 for pixel in guide_colors if pixel.alpha() < 20)
    assert guide_hits > 3, "股价中线必须有灰色虚线指向买卖交界"
    assert guide_gaps > 3, "股价引导线必须是虚线而不是实线"


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



def test_theme2_left_mirror_puts_price_right_and_depth_after_left_axis(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2", theme2_side="left", font_size=13))
    widget.set_quote(100.0, -0.75, QColor(154, 163, 184))
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
    mirror, axis_x, depth_inner_x, price_left, price_right = widget._horizontal_geometry()

    assert mirror is True
    assert axis_x == pytest.approx(5)
    assert axis_x < depth_inner_x < price_left < price_right
    assert widget._price_rect.center().x() > image.width() * 0.65

    colored_left_of_axis = 0
    upper_green_right = 0
    lower_red_right = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            green = _is_green(pixel)
            red = _is_red(pixel)
            if x < axis_x and (green or red):
                colored_left_of_axis += 1
            if x <= axis_x + 2:
                continue
            if y < center_y - 10:
                upper_green_right += int(green)
            elif y > center_y + 10:
                lower_red_right += int(red)

    colored_inside_price_side = 0
    for y in range(image.height()):
        for x in range(round(depth_inner_x) + 1, image.width()):
            pixel = image.pixelColor(x, y)
            if _is_green(pixel) or _is_red(pixel):
                colored_inside_price_side += 1

    assert colored_left_of_axis == 0, "镜像后挂单量条不能跑到左侧价格轴外"
    assert colored_inside_price_side == 0, "镜像后挂单不能侵入最右股价区域"
    assert upper_green_right > 0, "镜像后卖盘仍应在中线上方并向右延伸"
    assert lower_red_right > 0, "镜像后买盘仍应在中线下方并向右延伸"



def test_theme2_depth_width_changes_only_order_book_span(app):
    narrow = DepthLadder()
    narrow.apply_config(
        Config(display_theme="theme2", theme2_side="right", theme2_depth_width=60)
    )
    wide = DepthLadder()
    wide.apply_config(
        Config(display_theme="theme2", theme2_side="right", theme2_depth_width=220)
    )

    _, narrow_axis, narrow_inner, narrow_price_left, narrow_price_right = narrow._horizontal_geometry()
    _, wide_axis, wide_inner, wide_price_left, wide_price_right = wide._horizontal_geometry()

    assert narrow_axis - narrow_inner == pytest.approx(60)
    assert wide_axis - wide_inner == pytest.approx(220)
    assert wide.width() - narrow.width() == 160
    # 股价文字区本身不随盘口宽度变化，只把盘口区域拉宽。
    assert narrow_price_right - narrow_price_left == pytest.approx(
        wide_price_right - wide_price_left
    )
