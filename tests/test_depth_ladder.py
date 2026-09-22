from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import Qt
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



def test_theme2_depth_width_controls_natural_width_but_manual_resize_wins(app):
    narrow = DepthLadder()
    narrow.apply_config(
        Config(display_theme="theme2", theme2_side="right", theme2_depth_width=60)
    )
    wide = DepthLadder()
    wide.apply_config(
        Config(display_theme="theme2", theme2_side="right", theme2_depth_width=220)
    )

    # 配置只决定启动/自然宽度。
    assert wide.sizeHint().width() - narrow.sizeHint().width() == 160

    # 真正显示时，右下角拖出来的实际控件宽度决定盘口跨度。
    narrow.resize(240, 100)
    _, axis1, inner1, price_left1, price_right1 = narrow._horizontal_geometry()
    narrow.resize(400, 100)
    _, axis2, inner2, price_left2, price_right2 = narrow._horizontal_geometry()

    assert axis2 - inner2 > axis1 - inner1
    assert axis2 - inner2 - (axis1 - inner1) == pytest.approx(160)
    assert price_right1 - price_left1 == pytest.approx(price_right2 - price_left2)




class _ClickEvent:
    def __init__(self, x, y):
        from PySide6.QtCore import QPointF

        self._position = QPointF(x, y)
        self.accepted = False

    def button(self):
        return Qt.LeftButton

    def position(self):
        return self._position

    def accept(self):
        self.accepted = True


def test_theme2_price_click_target_covers_full_price_column(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2", theme2_side="right"))
    widget.resize(widget.sizeHint())
    widget.set_quote(100.0, 1.25, QColor(154, 163, 184))
    widget.grab()  # 触发 paintEvent，建立点击热区。

    mirror, _axis_x, _depth_inner_x, price_left, price_right = widget._horizontal_geometry()
    assert mirror is False
    assert widget._price_rect.top() == pytest.approx(0.0)
    assert widget._price_rect.bottom() == pytest.approx(widget.height())

    clicks = []
    widget.price_clicked.connect(lambda: clicks.append(True))

    # 即使点在价格列顶部/底部附近，也应容易展开，不要求精准点中文字。
    for y in (2, widget.height() / 2, widget.height() - 2):
        event = _ClickEvent((price_left + price_right) / 2, y)
        widget.mouseReleaseEvent(event)
        assert event.accepted is True

    assert len(clicks) == 3


def test_theme2_orderbook_area_does_not_trigger_price_click(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2", theme2_side="right"))
    widget.resize(widget.sizeHint())
    widget.set_quote(100.0, 1.25, QColor(154, 163, 184))
    widget.grab()

    clicks = []
    widget.price_clicked.connect(lambda: clicks.append(True))
    event = _ClickEvent(widget.width() - 10, widget.height() / 2)
    widget.mouseReleaseEvent(event)

    assert clicks == []
