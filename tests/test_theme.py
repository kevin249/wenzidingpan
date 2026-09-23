"""灰度显示：整块行情只剩**一个**可配置的灰阶。

用户要求：灰度模式下不要按原色分档（红一套灰、绿另一套灰），所有颜色统一成一个灰；
灰阶本身在设置页里可调。这里卡住这条语义——绘制端的像素校验见
``test_sparkline`` / ``test_depth_ladder``。
"""

from __future__ import annotations

from stockwidget.config import DEFAULT_GRAYSCALE_LEVEL, Config
from stockwidget.ui.theme import (
    FLAT,
    MUTED,
    direction_color,
    down_color,
    grayscale_color,
    text_color,
    up_color,
)


def test_grayscale_direction_color_is_one_gray_for_up_down_and_flat():
    config = Config(grayscale=True, grayscale_level=120)
    seen = {
        direction_color(config, change).getRgb()[:3]
        for change in (1.5, -1.5, 0.0, None)
    }
    assert seen == {(120, 120, 120)}, "涨 / 跌 / 平在灰度模式下不该再有区别"


def test_grayscale_also_swallows_user_configured_fixed_colors():
    """设置页里挑的固定色也要一起变灰，否则彩色会漏出灰度模式。"""
    config = Config(
        grayscale=True,
        grayscale_level=200,
        stock_price_color="#ff0000",
        stock_percent_color="auto",
        stock_name_color="#00ff00",
    )
    for setting in (config.stock_price_color, config.stock_percent_color, config.stock_name_color):
        automatic = direction_color(config, 1.0)
        assert text_color(config, setting, automatic).getRgb()[:3] == (200, 200, 200)


def test_colors_are_untouched_while_grayscale_is_off():
    config = Config(grayscale=False)

    assert text_color(config, "#123456", up_color(config)).getRgb()[:3] == (0x12, 0x34, 0x56)
    # auto 仍然跟随涨跌色，灰度感知版不许把它吃掉。
    assert text_color(config, "auto", up_color(config)) == up_color(config)

    assert direction_color(config, 1.0) == up_color(config)
    assert direction_color(config, -1.0) == down_color(config)
    assert direction_color(config, 0.0) == FLAT
    assert direction_color(config, None) == MUTED


def test_grayscale_color_clamps_whatever_level_it_is_given():
    assert grayscale_color(-20).getRgb()[:3] == (0, 0, 0)
    assert grayscale_color(999).getRgb()[:3] == (255, 255, 255)
    assert grayscale_color(DEFAULT_GRAYSCALE_LEVEL).getRgb()[:3] == (DEFAULT_GRAYSCALE_LEVEL,) * 3
    # 非数字也不能画出非法颜色。
    assert grayscale_color("nonsense").getRgb()[:3] == (DEFAULT_GRAYSCALE_LEVEL,) * 3
    # alpha 照旧可以就地覆盖：灰阶相同、层次靠透明度。
    assert grayscale_color(200, 88).alpha() == 88
    assert grayscale_color(200, 88).getRgb()[:3] == (200, 200, 200)
