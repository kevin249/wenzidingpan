"""主题2 行画布：以当前价虚线为中心，一侧盘口、另一侧图表。

画布本身没有背景（底色由主窗体给），所以像素断言统一走 :func:`_render`：先铺底色，
再以 ``DrawChildren`` 渲染——**不能**用默认 flags，因为 ``QWidget.render()`` 默认带
``DrawWindowBackground``，会把 Qt 调色板的浅灰窗口底（约 ``rgb(239,239,239)``）盖在
底色上，把半透明的盘口量条和虚线统统冲淡到判不出色。
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

try:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QMouseEvent, QRegion
    from PySide6.QtWidgets import QApplication, QWidget
except (ImportError, OSError) as error:
    pytest.skip(f"Qt 运行库不可用：{error}", allow_module_level=True)

from stockwidget.config import Config
from stockwidget.intraday import Trend
from stockwidget.kline import Bar, Kline
from stockwidget.mcp_depth import (
    DEPTH_ERROR_FAILURES,
    DepthLevel,
    DepthSnapshot,
)
from stockwidget.ui.depth_ladder import (
    CURVE_COLOR,
    DEPTH_SHARE_MIN,
    MODE_DAILY,
    MODE_INTRADAY,
    DepthLadder,
    book_colors,
)
from stockwidget.ui.theme import down_color, up_color

WIDTH = 405
HEIGHT = 380
BACKDROP = QColor(24, 27, 34)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _render(widget: DepthLadder) -> QImage:
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(BACKDROP)
    widget.render(image, QPoint(0, 0), QRegion(), QWidget.DrawChildren)
    return image


def _is_green(pixel) -> bool:
    """带绿相的像素：A 股配色下是日K 蜡烛的「涨」与**卖盘柱**（虚线上方）。

    盘口柱实测核心像素 ``rgb(28,87,55)``（叠在底色 ``rgb(24,27,34)`` 上，
    半透明 + 抗锯齿后的合成值），这里用色相判定而不是窄窗口，边缘行也能算命中。
    """
    return pixel.green() > pixel.red() + 30 and pixel.green() > pixel.blue() + 20


def _is_red(pixel) -> bool:
    """带红相的像素：日K 蜡烛的「跌」、**买盘柱**（虚线下方）与中线上的 ERROR 角标。

    买盘柱实测核心像素 ``rgb(101,45,54)``。
    """
    return pixel.red() > pixel.green() + 30 and pixel.red() > pixel.blue() + 20


def _matches(pixel, rgb, tol: int) -> bool:
    """单个像素是否落在 ``rgb`` 的 ±tol 窗口里。

    注意与文件后方的 ``_near(image, rgb, tolerance)``（整图计数）区分开：
    两者名字相近但用途完全不同，早前同名曾互相覆盖。
    """
    return all(abs(pixel.getRgb()[i] - rgb[i]) <= tol for i in range(3))


def _is_guide(pixel) -> bool:
    """当前价虚线：不透明的中性亮灰，实测 ``rgb(185,191,204)``。

    线是关抗锯齿画的，核心像素就是常量本身，所以这里收成窄窗口；
    比它更亮的只剩分时曲线（``rgb(214,220,232)``），靠 10px 容差自然分开。
    """
    return _matches(pixel, (185, 191, 204), 10)


def _is_grid(pixel) -> bool:
    """半小时竖虚线：叠在底色上实测 ``rgb(61,65,74)``，是全行最暗的图元。

    窗口收到 ±4：盘口柱（买 ``rgb(77,81,92)`` / 卖 ``rgb(96,101,112)``）、
    坐标轴（``rgb(56,59,68)``）与量柱都不在窗口里。
    """
    return _matches(pixel, (61, 65, 74), 4)


def _is_curve(pixel) -> bool:
    """分时曲线：固定中性色 ``CURVE_COLOR``，与涨跌方向色无关（用户口径）。"""
    return _matches(pixel, CURVE_COLOR.getRgb()[:3], 10)


def _curve_columns(image: QImage, x0: int, x1: int) -> list[int]:
    """落在 [x0, x1) 区间里、画到了曲线的横坐标。"""
    found: list[int] = []
    for x in range(max(0, x0), min(image.width(), x1)):
        for y in range(image.height()):
            if _is_curve(image.pixelColor(x, y)):
                found.append(x)
                break
    return found


def _rgb_points(image: QImage, rect, rgb, tol: int = 45):
    """某个矩形里画成了 ``rgb`` 的像素坐标（文字笔画的核心像素）。"""
    points = []
    for y in range(round(rect.top()), round(rect.bottom())):
        for x in range(round(rect.left()), round(rect.right())):
            if x < 0 or y < 0 or x >= image.width() or y >= image.height():
                continue
            if _matches(image.pixelColor(x, y), rgb, tol):
                points.append((x, y))
    return points


def _grid_hits(image: QImage, x: int) -> int:
    return sum(1 for y in range(image.height()) if _is_grid(image.pixelColor(x, y)))


def _guide_ratio(image: QImage, y: int, x0: float, x1: float) -> float:
    """某一横行上落在 [x0, x1) 区间内的虚线覆盖率。

    虚线是 4px 实 / 3px 空，理论覆盖约 57%；散落的文字笔画远达不到这个密度，
    所以用比例断言既能证明线「横贯」，又不会被顺带画在同一行的文字误判。
    """
    left, right = round(min(x0, x1)), round(max(x0, x1))
    total = max(1, right - left)
    hits = sum(1 for x in range(left, right) if _is_guide(image.pixelColor(x, y)))
    return hits / total


def _depth_band(layout) -> range:
    """盘口柱的横向扫描区间：从价格轴那一侧内缩 3px。

    内缩是为了避开价格轴自身那 1px 竖线（中性灰，本来就判不进买卖的色相窗口）与其
    抗锯齿过渡像素——扫描量行分布时不该把轴算成一根柱子。
    """
    lo, hi = sorted((round(layout.depth_edge), round(layout.axis_x)))
    if layout.axis_x >= layout.depth_edge:
        hi -= 3
    else:
        lo += 3
    return range(lo, hi)


def _depth_rows(image: QImage, band: range, test) -> list[int]:
    """落在 ``band`` 里满足 ``test`` 的横行（含重复，交给 ``clusters`` 去重）。

    ``test`` 传色相谓词 ``_is_green`` / ``_is_red``：A 股配色下卖盘在上为绿、
    买盘在下为红；欧美配色整体翻转。
    """
    return [
        y
        for y in range(image.height())
        for x in band
        if test(image.pixelColor(x, y))
    ]


def _click(widget: DepthLadder, x: float, y: float) -> None:
    point = QPointF(x, y)
    event = QMouseEvent(
        QEvent.MouseButtonRelease,
        point,
        point,
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    widget.mouseReleaseEvent(event)


def _levels(count: int = 8):
    return tuple(
        [DepthLevel("ask", 100.1 + index * 0.1, 120 + index * 50) for index in range(count)]
        + [DepthLevel("bid", 99.9 - index * 0.1, 140 + index * 45) for index in range(count)]
    )


def _depth(count: int = 8) -> DepthSnapshot:
    return DepthSnapshot(
        symbol="600000",
        available=True,
        levels=_levels(count),
        ask_count=count,
        bid_count=count,
    )


def _widget(**overrides) -> DepthLadder:
    quote_color = overrides.pop("quote_color", QColor(154, 163, 184))
    options = {"display_theme": "theme2", "theme2_side": "right", "font_size": 13}
    options.update(overrides)
    widget = DepthLadder()
    widget.apply_config(Config(**options))
    widget.resize(WIDTH, HEIGHT)
    widget.set_quote(100.0, 1.25, quote_color)
    return widget


def _session_trend(points: int = 241) -> Trend:
    """一次完整交易日是 241 点（9:30…15:00 含两端）。"""
    prices = [100 + (index % 41 - 20) * 0.05 for index in range(points)]
    return Trend(
        prices=prices,
        volumes=[100 + index for index in range(points)],
        prev_close=100.0,
        open_price=prices[0],
        high_price=max(prices),
        low_price=min(prices),
    )


def _text_runs(rows: list[int]) -> list[list[int]]:
    """把横行按连续性切成若干段：股价列里就是「股价」和「涨跌幅」两段。"""
    runs: list[list[int]] = []
    for y in sorted(set(rows)):
        if runs and y <= runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    return runs


def _trend() -> Trend:
    prices = [100 + (index % 7 - 3) * 0.4 for index in range(30)]
    return Trend(
        prices=prices,
        volumes=[100 + index * 10 for index in range(30)],
        prev_close=100.0,
        open_price=prices[0],
        high_price=max(prices),
        low_price=min(prices),
    )


def _kline() -> Kline:
    """日K 测试数据：涨跌交替，保证红绿两种蜡烛都画得出来。

    早前这里是清一色 ``open = close - 0.4``（全红），一旦要找「绿」就会空手而归；
    ``index % 3 == 2`` 的三分之一根换成阴线，红绿分布就不依赖运气了。
    """
    bars = []
    for index in range(20):
        close = 100 + index * 0.2
        open_ = close + 0.4 if index % 3 == 2 else close - 0.4
        bars.append(
            Bar(
                time=f"2026-06-{index + 1:02d}",
                open=open_,
                high=max(open_, close) + 0.5,
                low=min(open_, close) - 0.3,
                close=close,
                volume=1000 + index * 50,
            )
        )
    return Kline(symbol="600000", period="1d", bars=bars)


def _colored_rows(image: QImage, width: int):
    green_rows: list[int] = []
    red_rows: list[int] = []
    for y in range(image.height()):
        for x in range(width):
            pixel = image.pixelColor(x, y)
            if _is_green(pixel):
                green_rows.append(y)
            elif _is_red(pixel):
                red_rows.append(y)
    return green_rows, red_rows


# ---------------------------------------------------------------- 盘口买卖配色


def test_book_colors_map_bids_to_up_color_and_asks_to_down_color():
    """买盘跟「涨」色、卖盘跟「跌」色：A 股下就是下红上绿，欧美配色整体翻转。"""
    cn = Config(color_scheme="cn")
    bid, ask = book_colors(cn)
    assert bid == up_color(cn) and ask == down_color(cn)
    assert bid.red() > bid.green() and bid.red() > bid.blue(), "A 股买盘要偏红"
    assert ask.green() > ask.red() and ask.green() > ask.blue(), "A 股卖盘要偏绿"

    us = Config(color_scheme="us")
    assert book_colors(us) == (up_color(us), down_color(us))
    assert book_colors(us)[1].red() > book_colors(us)[1].green()


def test_theme2_color_mode_puts_asks_green_above_and_bids_red_below(app):
    """用户口径（2026-09-23）：「彩色模式上面显示卖绿色，下面显示买红色，A 股」。

    这条只在彩色模式下成立；灰度模式必须把两者塌成同一个灰（见上一条用例）。
    """
    widget = _widget()
    widget.set_depth(_depth())
    image = _render(widget)
    center = widget.height() // 2
    band = _depth_band(widget._layout())

    ask_rows = _depth_rows(image, band, _is_green)
    bid_rows = _depth_rows(image, band, _is_red)
    assert ask_rows, "虚线上方应当有绿色的卖盘柱"
    assert bid_rows, "虚线下方应当有红色的买盘柱"
    assert max(ask_rows) < center, "卖盘柱不许越过中线"
    assert min(bid_rows) > center, "买盘柱不许越过中线"
    widget.close()


def test_theme2_book_colors_follow_the_color_scheme(app):
    """切到欧美配色（绿涨红跌）时盘口整体翻转：上红下绿。"""
    widget = _widget(color_scheme="us")
    widget.set_depth(_depth())
    image = _render(widget)
    center = widget.height() // 2
    band = _depth_band(widget._layout())

    above_red = _depth_rows(image, band, _is_red)
    below_green = _depth_rows(image, band, _is_green)
    assert above_red and max(above_red) < center, "欧美配色下卖盘在上、应为红"
    assert below_green and min(below_green) > center, "欧美配色下买盘在下、应为绿"
    widget.close()


# ---------------------------------------------------------------- 灰度显示


def _near(image: QImage, rgb, tolerance: int = 6) -> int:
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if all(
            abs(channel - target) <= tolerance
            for channel, target in zip(image.pixelColor(x, y).getRgb()[:3], rgb)
        )
    )


def _dominant(image: QImage, band, rows) -> tuple:
    """``band`` × ``rows`` 里出现次数最多的非底色合成色。"""
    counts = Counter(
        image.pixelColor(x, y).getRgb()[:3]
        for y in rows
        for x in band
        if image.pixelColor(x, y) != BACKDROP
    )
    assert counts, "扫描区里没有任何非底色像素"
    return counts.most_common(1)[0][0]


def test_theme2_grayscale_puts_book_candles_and_price_on_one_gray(app):
    """灰度模式下整行只剩一个灰阶。

    盘口买卖本来一红一绿、K 线蜡烛跟着涨跌、股价还可能被设置成固定色——灰度模式下
    这些差别一笔抹掉，连用户挑的固定色也一起变灰，否则彩色会漏出来。
    """
    level = 210
    widget = _widget(grayscale=True, grayscale_level=level)
    widget.set_quote(
        100.0,
        1.25,
        QColor(154, 163, 184),
        None,
        price_color=QColor(240, 79, 90),
        percent_color=QColor(60, 120, 240),
    )
    widget.set_depth(_depth())
    widget.set_intraday(_session_trend())
    widget.set_kline(_kline())
    widget.set_expanded(True)
    image = _render(widget)

    green_rows, red_rows = _colored_rows(image, widget.width())
    assert (green_rows, red_rows) == ([], []), "灰度模式下不许再有红 / 绿像素"

    # 而且画出来的就是配置的那个灰阶（股价文字满不透明，最容易命中）。
    price_rect, _percent_rect = widget._price_blocks(widget._layout())
    assert any(
        image.pixelColor(x, y) == QColor(level, level, level)
        for y in range(round(price_rect.top()), round(price_rect.bottom()))
        for x in range(round(price_rect.left()), round(price_rect.right()))
    ), "股价文字应画成配置的灰阶"

    # 盘口买卖**同色**：上下半区各自出现最多的那个合成色必须一致——彩色模式下它们
    # 一个是绿一个是红，灰度模式必须把两边的差别彻底抹平（用户口径）。
    layout = widget._layout()
    band = _depth_band(layout)
    center = widget.height() // 2
    assert _dominant(image, band, range(0, center)) == _dominant(
        image, band, range(center + 1, image.height())
    ), "灰度下买盘与卖盘必须是同一个灰"

    # 日K 蜡烛同样只用一个灰。
    widget.set_chart_mode(MODE_DAILY)
    daily = _render(widget)
    green_rows, red_rows = _colored_rows(daily, widget.width())
    assert (green_rows, red_rows) == ([], []), "灰度的日K 蜡烛不该有红 / 绿"
    widget.close()


def test_theme2_candles_stay_colored_while_grayscale_is_off(app):
    """反证：同一套配置关掉灰度后日K 蜡烛确实还是红绿分明的，上面那条断言才不是空转。

    这里**故意不设盘口**：彩色模式下买卖柱原本也是红绿的，带上它就会让这条用例变成
    「怎么都通过」，反而证明不了蜡烛还在上色。
    """
    widget = _widget()
    widget.set_intraday(_session_trend())
    widget.set_kline(_kline())
    widget.set_chart_mode(MODE_DAILY)
    widget.set_expanded(True)

    green_rows, red_rows = _colored_rows(_render(widget), widget.width())
    assert green_rows and red_rows
    widget.close()


# ---------------------------------------------------------------- 折叠态


def test_theme2_price_guide_is_vertical_center_with_asks_above_bids_below(app):
    widget = _widget()
    widget.set_depth(_depth())
    image = _render(widget)
    center = widget.height() // 2
    band = _depth_band(widget._layout())

    ask_rows = _depth_rows(image, band, _is_green)
    bid_rows = _depth_rows(image, band, _is_red)
    assert ask_rows and max(ask_rows) < center, "卖盘必须在虚线上方"
    assert bid_rows and min(bid_rows) > center, "买盘必须在虚线下方"


def test_theme2_price_guide_is_dashed_and_reaches_across_the_row(app):
    widget = _widget()
    image = _render(widget)
    center = widget.height() // 2
    layout = widget._layout()

    span = range(round(layout.depth_edge) + 3, round(layout.price_left) - 6)
    hits = 0
    gaps = 0
    for x in span:
        pixel = image.pixelColor(x, center)
        if _is_guide(pixel):
            hits += 1
        elif pixel == BACKDROP:
            gaps += 1

    assert hits > 30, "当前价虚线要横贯盘口区"
    assert gaps > 10, "当前价线必须是虚线而不是实线"


def test_theme2_price_sits_above_the_guide_and_percent_below(app):
    """股价在虚线上方、涨跌幅在下方，且两块都水平居中在虚线线段的中点。

    用户口径（2026-09-23）：「股价和涨跌幅显示到虚线中间」——所以断言分两半，
    纵向夹着线、横向对中点，两个方向都要成立。
    """
    price_rgb = (240, 79, 90)
    percent_rgb = (60, 120, 240)
    for side in ("right", "left"):
        widget = _widget(theme2_side=side)
        widget.set_quote(
            100.0,
            1.25,
            QColor(154, 163, 184),
            None,
            price_color=QColor(*price_rgb),
            percent_color=QColor(*percent_rgb),
        )
        image = _render(widget)
        layout = widget._layout()
        center = widget.height() // 2
        price_rect, percent_rect = widget._price_blocks(layout)
        guide_left, guide_right = widget._guide_span(layout)
        middle = (guide_left + guide_right) / 2.0

        price_points = _rgb_points(image, price_rect, price_rgb)
        percent_points = _rgb_points(image, percent_rect, percent_rgb)
        assert price_points, f"{side} 股价没画出来"
        assert percent_points, f"{side} 涨跌幅没画出来"

        assert max(y for _x, y in price_points) < center - 1, "股价整段要在虚线上方"
        assert min(y for _x, y in percent_points) > center + 1, "涨跌幅整段要在虚线下方"

        # 水平方向：两块字面的中心都落在虚线线段的中点上（±2px 给字形左右不对称）。
        for name, points in (("股价", price_points), ("涨跌幅", percent_points)):
            xs = [x for x, _y in points]
            assert abs((min(xs) + max(xs)) / 2.0 - middle) <= 2.0, (
                f"{side} {name}没有居中在虚线中点：{(min(xs) + max(xs)) / 2.0} vs {middle}"
            )
        widget.close()


def test_theme2_price_and_percent_use_their_own_configured_colors(app):
    """股价 / 涨跌幅各画各的颜色，不再共用一个涨跌色。

    设置页里这两个字段各有固定色与「跟随涨跌」开关，主题1 走 ``text_color``；
    主题2 必须走同一条路，否则固定色被吃掉。
    """
    price_rgb = (240, 79, 90)
    percent_rgb = (60, 120, 240)
    widget = _widget(quote_color=QColor(154, 163, 184))
    widget.set_quote(
        100.0,
        1.25,
        QColor(154, 163, 184),
        None,
        price_color=QColor(*price_rgb),
        percent_color=QColor(*percent_rgb),
    )
    image = _render(widget)
    layout = widget._layout()
    price_rect, percent_rect = widget._price_blocks(layout)

    assert _rgb_points(image, price_rect, price_rgb), "股价要用它自己的颜色"
    assert _rgb_points(image, percent_rect, percent_rgb), "涨跌幅要用它自己的颜色"
    widget.close()


def test_theme2_intraday_curve_is_neutral_regardless_of_direction_color(app):
    """用户口径：整行只有股价 / 涨跌幅带涨跌色，分时曲线固定中性色。

    所以把方向色设成纯红，曲线也不许变红——这正是用户报的「k线啥的咋都是红色了」。
    """
    red = QColor(240, 0, 0)
    widget = _widget(quote_color=red)
    widget.set_quote(100.0, 1.25, red, None, price_color=red, percent_color=red)
    widget.set_intraday(_session_trend(121))
    widget.set_expanded(True)
    image = _render(widget)
    layout = widget._layout()
    center = widget.height() // 2

    columns = _curve_columns(image, round(layout.plot_left), round(layout.plot_right))
    assert columns, "曲线必须画出来"
    # 图表区（避开中线上的股价 / 涨跌幅文字）不许出现方向色。
    reds = [
        (x, y)
        for y in range(image.height())
        if abs(y - center) > 35
        for x in range(round(layout.plot_left), round(layout.plot_right))
        if _is_red(image.pixelColor(x, y))
    ]
    assert reds == [], f"方向色漏进了图表：{reds[:5]}"
    widget.close()


def test_theme2_show_stock_price_off_hides_the_text_but_keeps_the_guide(app):
    """设置页关掉「显示股价」时，两块文字都不画，当前价虚线照画。"""
    widget = _widget(show_stock_price=False)
    image = _render(widget)
    layout = widget._layout()
    center = widget.height() // 2
    price_rect, percent_rect = widget._price_blocks(layout)

    # 价格列已经不占版面，热区改成贴着价格轴的那条带（见 ``_price_hit_rect``）。
    band = widget._price_rect
    assert band.width() > 0
    if layout.mirror:
        assert band.left() == pytest.approx(layout.axis_x)
    else:
        assert band.right() == pytest.approx(layout.axis_x)
    assert not _rgb_points(image, price_rect, (154, 163, 184), tol=12), "关掉后不该画股价"
    assert not _rgb_points(image, percent_rect, (154, 163, 184), tol=12), "关掉后不该画涨跌幅"
    guide_left, guide_right = widget._guide_span(layout)
    assert _guide_ratio(image, center, guide_left + 2, guide_right - 2) > 0.4
    # 点击热区仍在：关掉文字不该把展开手势一起关掉。
    assert widget._price_rect.width() > 0
    widget.close()


def test_theme2_quote_error_text_lands_in_the_middle_price_block(app):
    """行情出错时的文案画在虚线中点那块。

    价格列已经不占版面（用户口径：「将空白移除」），错误文案没有别的落点，只能
    借中间那块文字位——否则一旦取数失败，行上会「什么都不显示」。
    """
    widget = _widget()
    widget.set_quote(100.0, 1.25, QColor(154, 163, 184), "网络不可达")
    image = _render(widget)
    price_rect, _percent_rect = widget._price_blocks(widget._layout())

    assert _rgb_points(image, price_rect, (139, 147, 167), tol=20), "错误文案应在中间那块里"
    widget.close()


def test_theme2_price_stays_above_the_error_badge_on_the_center_line(app):
    """用户要求：价格显示在 ERROR 上方——ERROR 占中线，价格整块让到线上方。"""
    widget = _widget()
    widget.set_depth(replace(_depth(), full_depth_failures=DEPTH_ERROR_FAILURES))
    assert widget._depth_error_active() is True
    image = _render(widget)
    layout = widget._layout()
    center = widget.height() // 2
    price_rect, percent_rect = widget._price_blocks(layout)

    # 让位是几何恒等式：出现 ERROR 时两块文字的间距各再让开半个角标高度。
    assert price_rect.bottom() < center - 1, "股价必须整块落在虚线上方"
    assert percent_rect.top() > center + 1, "涨跌幅必须整块落在虚线下方"

    # ERROR 角标反过来压在中线上：中线上下各 1px 的横行能扫到红色。
    assert any(
        _is_red(image.pixelColor(x, y))
        for y in (center - 1, center, center + 1)
        for x in range(image.width())
    ), "ERROR 角标应画在行的中线上"


def test_theme2_right_dock_puts_the_price_axis_on_the_right(app):
    widget = _widget(theme2_side="right")
    layout = widget._layout()

    assert layout.mirror is False
    # 价格列不再占版面（空白已并入图表），``price_left/price_right`` 收成价格轴那一点。
    assert layout.price_left == layout.price_right == layout.axis_x
    assert layout.axis_x == pytest.approx(WIDTH - 4.0), "价格轴贴右外沿"
    assert layout.depth_edge < layout.axis_x, "靠右停靠时盘口柱从价格轴向左长"


def test_theme2_left_dock_mirrors_the_price_axis_to_the_left(app):
    widget = _widget(theme2_side="left")
    layout = widget._layout()

    assert layout.mirror is True
    assert layout.price_left == layout.price_right == layout.axis_x
    assert layout.axis_x == pytest.approx(4.0), "价格轴贴左外沿"
    assert layout.depth_edge > layout.axis_x, "靠左停靠时盘口柱从价格轴向右长"


def test_theme2_collapsed_book_uses_configured_width_and_reserves_the_chart_half(app):
    widget = _widget()
    collapsed = widget._layout()
    book = abs(collapsed.depth_edge - collapsed.axis_x)
    span = abs(collapsed.axis_x - 4.0)

    # 折叠态不再铺满整行：盘口带长度取「配置宽度 84」与「可用跨度 25%」里更长的一个，
    # 另一半（含图表栏）留白——这是「点击不跳」的代价。
    assert book == pytest.approx(max(84.0, span * DEPTH_SHARE_MIN))
    assert collapsed.depth_edge > widget.width() / 2, "盘口柱不许越过行中点"
    # 图表栏即使折叠也必须预留，否则展开时柱长会被迫缩短。
    assert collapsed.kline_right - collapsed.kline_left >= 72
    assert collapsed.has_chart_band is True


def test_theme2_collapsed_state_leaves_the_reserved_chart_half_empty(app):
    widget = _widget()
    widget.set_intraday(_trend())
    widget.set_depth(_depth())
    image = _render(widget)
    layout = widget._layout()

    # 预留归预留，折叠时那一栏必须是空的：K 线、成交量柱都不许提前出现。
    colored = 0
    for y in range(image.height()):
        for x in range(
            round(layout.chart_left),
            round(min(layout.chart_right, layout.depth_edge)),
        ):
            pixel = image.pixelColor(x, y)
            if _is_green(pixel) or _is_red(pixel):
                colored += 1

    assert colored == 0, "折叠态不能画出 K 线或成交量"
    assert widget.expanded is False


def test_theme2_narrow_rows_keep_the_chart_alive(app):
    """窄行里「给 K 线留 72px」这条上限先于「不越中线」生效，图表不会被盘口挤没。

    价格列不再占版面之后，``MIN_DEPTH_LENGTH=26`` 那个兜底已经够不到了（它只在
    一行不足 60px 时才可能触发，而那时留白规则先一步把柱长压到 0），所以这条改守
    更靠前、也更有意义的那条：盘口永远给 K 线让出 ``MIN_KLINE_WIDTH``。
    """
    widget = _widget()
    for width in (190, 160, 120, 100):
        widget.resize(width, HEIGHT)
        layout = widget._layout()
        span = abs(layout.axis_x - 4.0)
        book = abs(layout.depth_edge - layout.axis_x)

        assert span - book >= 72 - 1e-6, f"{width}px 行里盘口吃掉了留给 K 线的宽度"
        assert book > 0, f"{width}px 行里盘口被压没了"
    widget.close()


def test_theme2_depth_width_controls_natural_width(app):
    narrow = DepthLadder()
    narrow.apply_config(Config(display_theme="theme2", theme2_depth_width=60))
    wide = DepthLadder()
    wide.apply_config(Config(display_theme="theme2", theme2_depth_width=220))

    assert wide.sizeHint().width() - narrow.sizeHint().width() == 160


def test_theme2_depth_levels_are_evenly_distributed_over_window_height(app):
    widget = _widget()
    widget.resize(260, 240)
    widget.set_depth(_depth())
    image = _render(widget)
    center = widget.height() // 2
    band = _depth_band(widget._layout())

    def clusters(values):
        values = sorted(set(values))
        groups = [[values[0]]]
        for value in values[1:]:
            if value <= groups[-1][-1] + 1:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [round(sum(group) / len(group)) for group in groups]

    asks = [y for y in clusters(_depth_rows(image, band, _is_green)) if y < center - 1]
    bids = [y for y in clusters(_depth_rows(image, band, _is_red)) if y > center + 1]
    assert len(asks) >= 8
    assert len(bids) >= 8

    ask_gaps = [b - a for a, b in zip(asks, asks[1:])]
    bid_gaps = [b - a for a, b in zip(bids, bids[1:])]
    assert max(ask_gaps) - min(ask_gaps) <= 3
    assert max(bid_gaps) - min(bid_gaps) <= 3
    assert min(asks) <= 5
    assert max(bids) >= image.height() - 5


# ---------------------------------------------------------------- 展开态


def test_theme2_expansion_puts_volume_on_the_opposite_side_of_the_book(app):
    right = _widget(theme2_side="right")
    right.set_intraday(_trend())
    right.set_expanded(True)
    layout = right._layout()

    # 靠右停靠：价格列与千档在右，成交量轴压在左外沿，K 线居中。
    assert layout.price_left > layout.kline_right
    assert layout.volume_axis == pytest.approx(4.0)
    assert layout.kline_left > layout.volume_axis + layout.volume_span - 1

    left = _widget(theme2_side="left")
    left.set_intraday(_trend())
    left.set_expanded(True)
    mirrored = left._layout()

    # 完全镜像：成交量轴压到右外沿，K 线仍在中间。
    assert mirrored.volume_axis == pytest.approx(WIDTH - 4)
    assert mirrored.price_right < mirrored.kline_left
    assert mirrored.kline_right < mirrored.volume_axis - mirrored.volume_span + 1


def test_theme2_expansion_keeps_the_book_bar_length_and_gives_the_chart_room(app):
    widget = _widget()
    collapsed = widget._layout()

    widget.set_expanded(True)
    expanded = widget._layout()

    # 用户红线：点击前后盘口柱长必须一模一样，展开只是把 K 线填进预留的那一半。
    assert abs(expanded.depth_edge - expanded.axis_x) == pytest.approx(
        abs(collapsed.depth_edge - collapsed.axis_x)
    )
    assert expanded.has_chart_band
    assert expanded.kline_right - expanded.kline_left >= 72


def test_clicking_never_moves_any_horizontal_band(app):
    """整张布局不含展开态分支，所以前后必须是同一个 dataclass。"""
    for side in ("right", "left"):
        for width in (405, 340, 260, 190):
            widget = _widget(theme2_side=side)
            widget.resize(width, HEIGHT)
            before = widget._layout()

            widget.set_expanded(True)
            after = widget._layout()

            assert after == before, f"{side}/{width} 点击改变了横向分区"
            widget.set_expanded(False)
            assert widget._layout() == before
            widget.close()


def test_theme2_bars_never_cross_the_row_midline(app):
    """两侧的柱各自留在自己那一半里（极窄行由最小可用长度兜底）。"""
    for side in ("right", "left"):
        for width in (405, 340, 260, 240):
            widget = _widget(theme2_side=side)
            widget.resize(width, HEIGHT)
            widget.set_intraday(_trend())
            widget.set_depth(_depth())
            widget.set_expanded(True)
            layout = widget._layout()
            middle = width / 2.0

            book_near, book_far = sorted((layout.axis_x, layout.depth_edge))
            if layout.mirror:
                assert book_far <= middle + 0.01, f"{side}/{width} 盘口柱越过中点"
            else:
                assert book_near >= middle - 0.01, f"{side}/{width} 盘口柱越过中点"

            vol_near, vol_far = sorted(
                (layout.volume_axis, layout.volume_axis + (layout.volume_span if not layout.mirror else -layout.volume_span))
            )
            assert vol_far <= middle + 0.01 or vol_near >= middle - 0.01, (
                f"{side}/{width} 成交量柱跨过中点"
            )
            widget.close()


def test_theme2_expanded_guide_line_still_spans_the_whole_row(app):
    widget = _widget()
    widget.set_expanded(True)
    image = _render(widget)
    center = widget.height() // 2
    layout = widget._layout()

    if layout.mirror:
        volume_band = (layout.volume_axis - layout.volume_span + 2, layout.volume_axis - 2)
        depth_band = (layout.axis_x + 2, layout.depth_edge - 2)
    else:
        volume_band = (layout.volume_axis + 2, layout.volume_axis + layout.volume_span - 2)
        depth_band = (layout.depth_edge + 2, layout.axis_x - 2)
    kline_band = (layout.kline_left + 2, layout.kline_right - 2)

    for name, band in (
        ("成交量栏", volume_band),
        ("K线栏", kline_band),
        ("盘口栏", depth_band),
    ):
        ratio = _guide_ratio(image, center, *band)
        assert ratio > 0.4, f"{name} 缺少贯穿的当前价虚线（覆盖率 {ratio:.2f}）"

    # 上下各偏 3px 就不该再有虚线：证明这条线恰好压在垂直中心。
    for offset in (-3, 3):
        for band in (volume_band, kline_band, depth_band):
            assert _guide_ratio(image, center + offset, *band) < 0.15


def test_theme2_price_band_stays_at_the_vertical_center_after_expansion(app):
    widget = _widget()
    center = widget.height() // 2
    collapsed = _render(widget)
    collapsed_layout = widget._layout()

    # 折叠态：盘口带长度与展开态相同（见 _depth_share），虚线照样横贯其间。
    assert _guide_ratio(
        collapsed, center, collapsed_layout.depth_edge + 2, collapsed_layout.axis_x - 2
    ) > 0.4

    widget.set_expanded(True)
    expanded = _render(widget)
    layout = widget._layout()
    band = (layout.depth_edge + 2, layout.axis_x - 2)

    assert _guide_ratio(expanded, center, *band) > 0.4, "展开不能挪动当前价虚线"
    assert _guide_ratio(expanded, center - 3, *band) < 0.15
    assert _guide_ratio(expanded, center + 3, *band) < 0.15


def test_theme2_daily_mode_draws_candles_in_the_middle_band(app):
    widget = _widget()
    widget.set_kline(_kline())
    widget.set_chart_mode(MODE_DAILY)
    widget.set_expanded(True)
    image = _render(widget)
    layout = widget._layout()

    colored = 0
    for y in range(image.height()):
        for x in range(round(layout.kline_left), round(layout.kline_right)):
            pixel = image.pixelColor(x, y)
            if _is_green(pixel) or _is_red(pixel):
                colored += 1

    assert colored > 50, "日K 应在中间的 K 线栏画出实体"


def test_theme2_daily_candles_span_between_the_two_axes(app):
    """横轴＝两根竖轴之间那一段，分时与日K 共用，所以切模式时图表宽度不跳。"""
    for side in ("right", "left"):
        widget = _widget(theme2_side=side)
        widget.set_kline(_kline())
        widget.set_chart_mode(MODE_DAILY)
        widget.set_expanded(True)
        image = _render(widget)
        layout = widget._layout()

        colored = [
            x
            for x in range(image.width())
            for y in range(image.height())
            if _is_green(image.pixelColor(x, y)) or _is_red(image.pixelColor(x, y))
        ]
        assert colored, f"{side} 日K 没画出实体"
        assert min(colored) <= layout.plot_left + 12, "日K 左端要贴到左轴"
        assert max(colored) >= layout.plot_right - 12, "日K 右端要贴到右轴"
        widget.close()


# ---------------------------------------------------------------- 时间轴与网格


def test_theme2_half_hour_gridlines_align_with_the_two_axes(app):
    """用户要求：每半小时一条竖线，9:30 对左轴、15:00 对右轴。

    横轴固定为一个完整交易日，所以这条对齐关系是几何恒等式，不是靠目测。
    """
    for side in ("right", "left"):
        widget = _widget(theme2_side=side)
        widget.set_expanded(True)
        layout = widget._layout()
        xs = widget._grid_xs(layout)

        assert len(xs) == 9, f"{side} 240 分钟按 30 分钟切应得 9 条刻度"
        assert xs[0] == pytest.approx(layout.plot_left)
        assert xs[-1] == pytest.approx(layout.plot_right)
        # 左轴就是成交量轴、右轴就是价格轴：两条竖线正好压在两根轴上。
        assert min(layout.volume_axis, layout.axis_x) == pytest.approx(xs[0])
        assert max(layout.volume_axis, layout.axis_x) == pytest.approx(xs[-1])

        gaps = [b - a for a, b in zip(xs, xs[1:])]
        assert max(gaps) - min(gaps) < 1e-6, "半小时刻度必须等距"
        # 11:30 与 13:00 压缩成同一条线，落在正中。
        assert xs[4] == pytest.approx((layout.plot_left + layout.plot_right) / 2)
        widget.close()


def test_theme2_gridlines_are_dashed_and_only_in_expanded_intraday(app):
    widget = _widget(quote_color=QColor(0, 128, 255))
    widget.set_intraday(_session_trend())
    layout = widget._layout()
    probe = round(widget._grid_xs(layout)[3])

    collapsed = _render(widget)
    assert _grid_hits(collapsed, probe) == 0, "折叠态不该出现竖虚线"

    widget.set_expanded(True)
    expanded = _render(widget)
    hits = _grid_hits(expanded, probe)
    assert hits > expanded.height() * 0.3, "展开后该有贯穿的竖虚线"
    assert hits < expanded.height() * 0.75, "竖线必须是虚线而不是实线"

    widget.set_kline(_kline())
    widget.set_chart_mode(MODE_DAILY)
    daily = _render(widget)
    assert _grid_hits(daily, probe) == 0, "日K 的横轴是日期，不该有半小时竖线"
    widget.close()


def test_theme2_intraday_axis_is_pinned_to_the_full_session(app):
    """横轴固定 9:30→15:00：半天数据只画到正中，整天才铺满。"""
    widget = _widget(quote_color=QColor(0, 128, 255))
    widget.set_expanded(True)
    layout = widget._layout()
    middle = (layout.plot_left + layout.plot_right) / 2

    # 121 点 = 9:30…11:30，末点必须落在 11:30 那条线上（也是正中）。
    assert widget._intraday_x(layout, 120, 121) == pytest.approx(middle)
    # 241 点 = 完整一天，末点落在右轴 15:00 上。
    assert widget._intraday_x(layout, 240, 241) == pytest.approx(layout.plot_right)
    # 刚开盘 31 分钟：只占整轴的八分之一。
    assert widget._intraday_x(layout, 30, 31) == pytest.approx(
        layout.plot_left + layout.plot_span / 8
    )
    widget.close()


def test_theme2_intraday_curve_reaches_both_axes(app):
    widget = _widget(quote_color=QColor(0, 128, 255))
    widget.set_intraday(_session_trend())
    widget.set_expanded(True)
    image = _render(widget)
    layout = widget._layout()

    columns = _curve_columns(image, 0, image.width())
    assert columns, "分时曲线没画出来"
    assert min(columns) <= layout.plot_left + 3, "曲线左端要贴到 9:30 那条轴上"
    assert max(columns) >= layout.plot_right - 3, "曲线右端要贴到 15:00 那条轴上"
    widget.close()


def test_theme2_curve_is_drawn_over_the_order_book_bars(app):
    """曲线铺满整条轴，会经过盘口带；它必须是最后画的，不能被量条切断。"""
    widget = _widget(quote_color=QColor(0, 128, 255))
    widget.set_intraday(_session_trend())
    widget.set_depth(_depth())
    widget.set_expanded(True)
    image = _render(widget)
    layout = widget._layout()

    lo, hi = sorted((layout.depth_edge, layout.axis_x))
    inside = _curve_columns(image, round(lo) + 4, round(hi) - 4)
    assert inside, "曲线没有延伸到盘口带"
    # 连续覆盖：盘口量条若压在曲线上，会在这里留下断口。
    gaps = [b - a for a, b in zip(inside, inside[1:]) if b - a > 1]
    assert len(gaps) <= 2, f"曲线被盘口量条切成了 {len(gaps)} 段"
    widget.close()


def test_theme2_chart_click_toggles_between_intraday_and_daily(app):
    widget = _widget()
    widget.set_expanded(True)
    changes = []
    widget.chart_mode_changed.connect(changes.append)

    assert widget.chart_mode == MODE_INTRADAY
    widget.toggle_chart_mode()
    assert widget.chart_mode == MODE_DAILY
    widget.toggle_chart_mode()
    assert widget.chart_mode == MODE_INTRADAY
    assert changes == [MODE_DAILY, MODE_INTRADAY]

    # 非法取值必须回落到分时，绝不留下一个画不出来的模式。
    widget.set_chart_mode("nonsense")
    assert widget.chart_mode == MODE_INTRADAY


def test_theme2_chart_message_is_drawn_while_daily_is_loading(app):
    widget = _widget()
    widget.set_chart_mode(MODE_DAILY)
    widget.set_kline(None, "日K 加载中…")
    widget.set_expanded(True)

    assert widget.kline is None
    _render(widget)  # 不应抛异常


# ---------------------------------------------------------------- 交互


def test_theme2_price_click_target_covers_full_price_column(app):
    widget = _widget()
    widget.set_depth(_depth())
    _render(widget)

    layout = widget._layout()
    assert widget._price_rect.top() == pytest.approx(0.0)
    assert widget._price_rect.bottom() == pytest.approx(widget.height())

    clicks: list[bool] = []
    widget.price_clicked.connect(lambda: clicks.append(True))
    for y in (2, widget.height() / 2, widget.height() - 2):
        _click(widget, (layout.price_left + layout.price_right) / 2, y)

    assert len(clicks) == 3


def test_theme2_price_band_is_the_click_target_and_the_deep_book_is_not(app):
    """热区＝贴着价格轴的那条价格带（宽度＝原价格列宽），盘口深处仍不响应点击。

    价格列不再占版面之后，热区只能贴着轴站——它必然压住盘口柱的根部，这是
    「空白被移除」的代价；柱身更深处与图表栏依旧点什么都不发生。
    """
    widget = _widget()
    widget.set_depth(_depth())
    _render(widget)

    events: list[str] = []
    widget.price_clicked.connect(lambda: events.append("price"))
    widget.chart_mode_changed.connect(lambda _mode: events.append("chart"))

    layout = widget._layout()
    band = widget._price_hit_rect()
    assert band.width() > 0

    deep = round(layout.depth_edge + 4) if layout.mirror else round(layout.depth_edge - 4)
    _click(widget, deep, widget.height() / 2)
    assert events == [], "盘口柱深处不该是点击热区"

    _click(widget, band.center().x(), widget.height() / 2)
    assert events == ["price"], "价格带上点一下就是展开/收起"


def test_theme2_collapsed_blank_area_does_not_switch_chart_mode(app):
    widget = _widget()
    _render(widget)
    layout = widget._layout()

    widget.price_clicked.connect(lambda: None)
    _click(widget, layout.axis_x - 40 if not layout.mirror else layout.axis_x + 40,
           widget.height() / 2)

    assert widget.chart_mode == MODE_INTRADAY
    assert widget.expanded is False


def test_theme2_chart_area_click_switches_mode_only_when_expanded(app):
    widget = _widget()
    widget.set_expanded(True)
    _render(widget)
    layout = widget._layout()

    _click(widget, (layout.kline_left + layout.kline_right) / 2, widget.height() - 6)
    assert widget.chart_mode == MODE_DAILY

    _click(widget, (layout.kline_left + layout.kline_right) / 2, 6)
    assert widget.chart_mode == MODE_INTRADAY


def test_theme2_depth_error_activates_on_third_consecutive_thousand_miss(app):
    widget = DepthLadder()
    widget.apply_config(Config(display_theme="theme2"))
    cached_levels = (
        DepthLevel("ask", 100.1, 120),
        DepthLevel("bid", 99.9, 140),
    )

    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=cached_levels,
            available=True,
            full_depth=True,
            depth_mode="FULL_DEPTH",
            full_depth_failures=2,
            using_cached_full_depth=True,
            latest_depth_mode="TEN_LEVEL",
        )
    )
    assert widget._depth_error_active() is False
    assert "连续2次" in widget.toolTip()

    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=cached_levels,
            available=True,
            full_depth=True,
            depth_mode="FULL_DEPTH",
            full_depth_failures=3,
            using_cached_full_depth=True,
            latest_depth_mode="TEN_LEVEL",
        )
    )
    assert widget._depth_error_active() is True
    assert "连续3次" in widget.toolTip()

    widget.set_depth(
        DepthSnapshot(
            symbol="600000",
            levels=cached_levels,
            available=True,
            full_depth=True,
            depth_mode="FULL_DEPTH",
            full_depth_failures=0,
            using_cached_full_depth=False,
            latest_depth_mode="FULL_DEPTH",
        )
    )
    assert widget._depth_error_active() is False
    assert widget.toolTip().startswith("盘口：千档")
