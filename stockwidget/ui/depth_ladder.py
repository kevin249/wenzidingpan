"""主题2 行画布：以当前价虚线为中心，一侧盘口、另一侧图表。

分工（``theme2_side="left"`` 时整体镜像）：

* 行顶部信息条（名字 / 代码 / 暗盘 / 高低）由 ``QuoteRow`` 的标签画在画布上方，
  且**收起时整条留空**（点开才出现）；
* 本控件画竖直价格轴、贯穿整行的当前价虚线、围绕虚线的千档挂单，以及落在虚线
  中点上的股价 / 涨跌幅；**价格列不占版面**（用户口径：「将空白移除」），竖直
  价格轴直接压在行外沿，空出来的宽度全给图表栏；
* 点击价格列展开后，盘口的**另一侧**多出一栏：贴外沿的成交量轴 + 中间的分时
  曲线或日K 蜡烛；
* 三栏共用同一条价格刻度，且当前价恒定落在控件垂直中点上，所以那条虚线在
  折叠与展开两种状态下都恰好是整行的水平中心；
* 股价与涨跌幅**水平居中在虚线线段的中点**，一上一下夹着那条线（用户口径：
  「显示到虚线中间」）；虚线本身那一行归红色 ``ERROR`` 角标用，一旦千档失败、
  角标出现，两块文字会自动再让开一点，所以价格永远在 ``ERROR`` 之上。

三条硬约束：

1. **柱长与展开态无关。** 盘口带的宽度只由配置和几何决定，点击展开只是把
   K 线填进早就预留好的另一半，柱子一个像素都不动——否则点击时整行都在跳。
2. **两侧的柱都不许越过行的水平中点。** 盘口量条与成交量柱各自留在自己那一半
   里（见 :meth:`DepthLadder._depth_share`）。
3. **分时的横轴就是两根竖轴之间那一段**（``plot_left`` → ``plot_right``）：
   左端固定 9:30、右端固定 15:00，午休压缩掉，所以每 30 分钟一条竖虚线，
   而首末两条恰好压在成交量轴与价格轴上。曲线也铺满这一段，于是竖线与曲线的
   时刻严格对应。

盘口量条按档位顺序平均分布（不按绝对价差压缩），这条规则同样与展开与否无关，
所以展开时其它档位不会因为多了一栏图表而跳动。
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..config import Config
from ..intraday import Trend
from ..kline import Bar, Kline
from ..mcp_depth import (
    DEPTH_ERROR_FAILURES,
    DEPTH_FIVE,
    DEPTH_FULL,
    DEPTH_TEN,
    DepthSnapshot,
)
from .sparkline import ANNOTATION_COLOR
from .sparkline import BUY_COLOR as BS_BUY_COLOR
from .sparkline import SELL_COLOR as BS_SELL_COLOR
from .theme import MUTED, display_color, down_color, make_font, up_color

# 主题2 的配色口径（用户口径 2026-09-23，v3）：分两种模式说话。
#
# **彩色模式**：盘口买卖柱用涨跌色——**买盘（虚线下方）跟「涨」色、卖盘（虚线上方）
# 跟「跌」色**，A 股习惯下就是「上绿下红」，与主题1 走势图右侧那排挂单柱同一套
# （见 :func:`book_colors`）。其余元素保持中性、靠亮度分层，从亮到暗依次是
# 分时曲线 > 当前价虚线 > 成交量柱 > 网格（全中性会糊成一片，这条分层不能省）。
# 名称、代码、高低价与分时曲线依旧不跟涨跌色。
#
# **灰度模式**：整行统一成 ``grayscale_level`` 那**一个**灰阶，盘口买卖**同色**，
# 方向只靠上下半区的位置区分（见 :meth:`DepthLadder._paint_color`）。
CURVE_COLOR = QColor(214, 220, 232)  # 分时曲线：全行最亮的中性色
DEPTH_ALPHA = 150  # 盘口买卖柱共用同一个透明度：方向由色相表达（灰度下由位置表达）
ERROR_COLOR = QColor(240, 79, 90)  # ERROR 角标：错误提示红，与涨跌无关
GUIDE_COLOR = QColor(185, 191, 204)
AXIS_COLOR = QColor(130, 136, 150)
VOLUME_COLOR = QColor(148, 163, 184)
GRID_COLOR = QColor(150, 156, 170)
GRID_ALPHA = 75
AXIS_ALPHA = 135
GUIDE_ALPHA = 255  # 当前价虚线不透明：它是全行唯一的价格基准，不能被底色冲淡
BADGE_BACKDROP = QColor(255, 255, 255, 34)  # 「分时 / 日K」角标的半透明底


def book_colors(config: Config) -> tuple[QColor, QColor]:
    """盘口买卖柱的颜色，返回 ``(买盘, 卖盘)``。

    买盘跟「涨」色、卖盘跟「跌」色：A 股习惯（``color_scheme="cn"``，默认）下就是
    **虚线上方卖盘绿、下方买盘红**；切到欧美配色则整体翻转，与日K 蜡烛同一条规则。

    灰度模式不在这里处理——:meth:`DepthLadder._paint_color` 会把两者都换成同一个
    灰阶，买卖同色、方向只由上下半区的位置承担。
    """
    return up_color(config), down_color(config)


MODE_INTRADAY = "intraday"
MODE_DAILY = "daily"
MODE_LABELS = {MODE_INTRADAY: "分时", MODE_DAILY: "日K"}
MODE_ORDER = (MODE_INTRADAY, MODE_DAILY)

# 当日交易分钟数：9:30–11:30 与 13:00–15:00 各 120 分钟，午休被压缩掉。
# 所以 11:30 与 13:00 共用同一条竖线，且正好落在时间轴正中。
SESSION_MINUTES = 240
GRID_STEP_MINUTES = 30  # 竖虚线间隔：每半小时一条

OUTER_PAD = 4.0
INNER_PAD = 2.0
LEVEL_GAP = 4.0  # 买卖交界与最近一档的间距
MIN_KLINE_WIDTH = 72.0  # 必须给 K 线留出的宽度
MIN_DEPTH_LENGTH = 26.0  # 中点约束下盘口柱的最小可用长度（极窄行才触发）
DEPTH_SHARE_MIN = 0.25
DEPTH_SHARE_MAX = 0.70
PRICE_LINE_GAP = 2.0  # 股价文字下沿 / 涨跌幅上沿与当前价虚线的间距
PRICE_BLOCK_PAD = 6.0  # 股价 / 涨跌幅文字块左右各留的余量


@dataclass(frozen=True)
class _Layout:
    """一次绘制用到的全部横向分区（单位 px，左闭右开）。"""

    mirror: bool  # True = 价格与千档靠左
    price_left: float
    price_right: float
    axis_x: float  # 竖直价格轴
    depth_edge: float  # 盘口量条最远可达的边界
    chart_left: float
    chart_right: float
    volume_axis: float  # 成交量柱的贴靠轴
    volume_span: float
    kline_left: float
    kline_right: float
    plot_left: float  # 分时/日K 横轴左端（= 两根竖轴中靠左的那根）
    plot_right: float  # 分时/日K 横轴右端（= 两根竖轴中靠右的那根）

    @property
    def plot_span(self) -> float:
        return self.plot_right - self.plot_left

    @property
    def has_chart_band(self) -> bool:
        """图表栏宽度是否可用。

        注意这是**布局**属性：折叠态也照样预留这一栏（只是不画），所以它恒为真；
        判断「当前要不要画图」请用 :attr:`expanded`。
        """
        return self.kline_right - self.kline_left >= 2.0


class DepthLadder(QWidget):
    """主题2 的整行主体：价格列 + 千档盘口（展开后追加图表栏）。"""

    price_clicked = Signal()
    chart_mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._depth: DepthSnapshot | None = None
        self._price: float | None = None
        self._percent: float | None = None
        self._price_color = QColor(220, 224, 234)
        self._percent_color = QColor(220, 224, 234)
        self._error = ""
        self._config = Config()
        self._expanded = False
        self._chart_mode = MODE_INTRADAY
        self._trend: Trend | None = None
        self._prev_close: float | None = None
        self._kline: Kline | None = None
        self._kline_message = ""
        self._signals: list[tuple[int, str]] = []
        self._show_signals = True
        self._preferred_width = 168
        self._preferred_height = 104
        self._price_rect = QRectF()
        self._chart_rect = QRectF()
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumWidth(0)
        self.setMinimumHeight(52)

    # ------------------------------------------------------------ 配置

    def apply_config(self, config: Config) -> None:
        self._config = config
        self._preferred_width = self._natural_width()
        self._preferred_height = max(88, round(config.font_size * 7.2))
        self.updateGeometry()
        self.update()

    def _metrics(self) -> tuple[float, float, float]:
        """返回 (外边距, 价格带宽度, 价格带与价格轴之间的间距)。

        第二个值现在只用来定**点击热区**的宽度与 :meth:`_natural_width` 的余量：
        价格列已经不占版面了（见 :meth:`_layout`）。
        """
        price_width = max(66.0, round(self._config.stock_price_font_size * 4.6))
        gap = max(6.0, round(self._config.font_size * 0.45))
        return OUTER_PAD, price_width, gap

    def _natural_width(self) -> int:
        outer, price_width, gap = self._metrics()
        depth = max(40, int(self._config.theme2_depth_width or 0))
        return int(outer + price_width + gap + depth + outer + 5)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(max(self._preferred_width, 120), self._preferred_height)

    # ------------------------------------------------------------ 状态

    @property
    def expanded(self) -> bool:
        return self._expanded

    @property
    def chart_mode(self) -> str:
        return self._chart_mode

    @property
    def kline(self) -> Kline | None:
        return self._kline

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            return
        self._expanded = expanded
        if not expanded:
            self._chart_rect = QRectF()
        self.update()

    def set_chart_mode(self, mode: str, *, notify: bool = True) -> None:
        mode = mode if mode in MODE_LABELS else MODE_INTRADAY
        if mode == self._chart_mode:
            return
        self._chart_mode = mode
        self.update()
        if notify:
            self.chart_mode_changed.emit(mode)

    def toggle_chart_mode(self) -> None:
        index = MODE_ORDER.index(self._chart_mode)
        self.set_chart_mode(MODE_ORDER[(index + 1) % len(MODE_ORDER)])

    def set_depth(self, snapshot: DepthSnapshot | None) -> None:
        if snapshot != self._depth:
            self._depth = snapshot
            if snapshot and snapshot.using_cached_full_depth:
                fallback = {
                    DEPTH_TEN: "十档",
                    DEPTH_FIVE: "五档",
                }.get(snapshot.latest_depth_mode, "不可用")
                mode = (
                    f"千档缓存 · 连续{snapshot.full_depth_failures}次未获取到千档"
                    f" · 当前回退{fallback} · 5秒重试中"
                )
            else:
                mode = {
                    DEPTH_FULL: "千档",
                    DEPTH_TEN: "十档（千档5秒重试中）",
                    DEPTH_FIVE: "五档（千档5秒重试中）",
                }.get(snapshot.depth_mode if snapshot else "", "盘口不可用")
            self.setToolTip(f"盘口：{mode}｜点击价格列展开/收起K线｜展开后点K线切换分时与日K")
            self.update()

    def _depth_error_active(self) -> bool:
        return bool(
            self._depth
            and self._depth.full_depth_failures >= DEPTH_ERROR_FAILURES
        )

    def set_quote(
        self,
        price: float | None,
        percent: float | None,
        color: QColor,
        error: str | None = None,
        *,
        price_color: QColor | None = None,
        percent_color: QColor | None = None,
    ) -> None:
        """``color`` 是涨跌方向色，只当「股价 / 涨跌幅」缺省颜色的回退。

        ``price_color`` / ``percent_color`` 是设置页里「股价 / 涨跌幅」各自的颜色
        （固定色或跟随涨跌）。

        主题2 的分时曲线**不再**吃这个方向色（用户口径 2026-09-23）：曲线固定走
        :data:`CURVE_COLOR` 中性色，整行只有这两块文字带涨跌色，所以「涨=红」时
        不会连带把名称、曲线、千档一起染红。
        """
        resolved_price = QColor(price_color) if price_color is not None else QColor(color)
        resolved_percent = (
            QColor(percent_color) if percent_color is not None else QColor(color)
        )
        state = (
            price,
            percent,
            resolved_price.rgba(),
            resolved_percent.rgba(),
            str(error or ""),
        )
        old = (
            self._price,
            self._percent,
            self._price_color.rgba(),
            self._percent_color.rgba(),
            self._error,
        )
        if state != old:
            self._price = price
            self._percent = percent
            self._price_color = resolved_price
            self._percent_color = resolved_percent
            self._error = str(error or "")
            self.update()

    def set_intraday(self, trend: Trend | None, prev_close: float | None = None) -> None:
        """分时曲线数据；``prev_close`` 缺省时沿用行情快照里的昨收。"""
        resolved = (trend.prev_close if trend and trend.prev_close else None) or prev_close
        state = (
            list(trend.prices) if trend else None,
            list(trend.volumes) if trend else None,
            resolved,
        )
        old = (
            list(self._trend.prices) if self._trend else None,
            list(self._trend.volumes) if self._trend else None,
            self._prev_close,
        )
        if state != old:
            self._trend = trend
            self._prev_close = resolved
            self.update()

    def set_kline(self, kline: Kline | None, message: str = "") -> None:
        self._kline = kline
        self._kline_message = message
        self.update()

    def set_signals(self, signals=None, *, show: bool = True) -> None:
        """分时曲线上的 B/S 转折标记（索引对应 :meth:`set_intraday` 的价格序列）。

        日K 模式不画——日报的横轴是日期，和分钟索引对不上。
        """
        new = list(signals or [])
        if new == self._signals and bool(show) == self._show_signals:
            return
        self._signals = new
        self._show_signals = bool(show)
        self.update()

    # ------------------------------------------------------------ 字号

    def _popup_font(self):
        """图表区所有小字（「量」标注、模式角标、加载提示）的统一字号。"""
        return make_font(
            self._config, pixel_size=max(9, self._config.theme2_popup_font_size)
        )

    # ------------------------------------------------------------ 几何

    def _layout(self) -> _Layout:
        """横向分区的唯一真源。

        整张布局**不含任何展开态判断**：折叠与展开共用同一套分区，只是折叠时不画
        图表。这样「点击不跳」不是靠两边算得一样来保证的，而是结构上不可能不同。
        """
        width = float(max(1, self.width()))
        outer, _price_width, gap = self._metrics()
        mirror = self._config.theme2_side == "left"

        # 价格列**不占版面**（用户口径 2026-09-23：「将红色 X 处的空白移除」）。
        # 股价与涨跌幅已经画在虚线线段的中点（见 :meth:`_price_blocks`），再留一列
        # 就只剩一条空白带——它约占整行 20%，纯浪费。竖直价格轴因此贴到行外沿，
        # 空出来的宽度全部并入图表栏。``price_left/price_right`` 保留为同一个点
        # （= 价格轴本身），老调用点与热区都还认它。
        if mirror:
            price_left = price_right = outer
            axis_x = price_left
            span = max(0.0, width - outer - axis_x)
        else:
            price_left = price_right = max(outer, width - outer)
            axis_x = price_left
            span = max(0.0, axis_x - outer)

        share = self._depth_share(span, axis_x, width)

        if mirror:
            depth_edge = axis_x + share
            chart_left = min(width - outer, depth_edge + gap)
            chart_right = max(chart_left, width - outer)
            volume_axis = chart_right
        else:
            depth_edge = axis_x - share
            chart_right = max(outer, depth_edge - gap)
            chart_left = outer
            volume_axis = chart_left

        volume_span = self._volume_span(max(0.0, chart_right - chart_left))
        if mirror:
            kline_right = max(chart_left, chart_right - volume_span - gap)
            kline_left = chart_left
        else:
            kline_left = min(chart_right, chart_left + volume_span + gap)
            kline_right = chart_right

        return _Layout(
            mirror=mirror,
            price_left=price_left,
            price_right=price_right,
            axis_x=axis_x,
            depth_edge=depth_edge,
            chart_left=chart_left,
            chart_right=chart_right,
            volume_axis=volume_axis,
            volume_span=volume_span,
            kline_left=kline_left,
            kline_right=kline_right,
            plot_left=min(axis_x, volume_axis),
            plot_right=max(axis_x, volume_axis),
        )

    def _depth_share(self, span: float, axis_x: float, width: float) -> float:
        """盘口量条的最大长度（px）。

        与展开态无关，所以点击前后柱长严格相等。三个上限由弱到强：

        1. 配置的 ``theme2_depth_width``，并夹在可用跨度的 25%~70% 之间；
        2. 给 K 线留出 :data:`MIN_KLINE_WIDTH`，图表不能被盘口挤没；
        3. **不许越过行的水平中点**——两侧的柱各自留在自己那一半里。

        第 3 条在极窄行里会把柱压到几乎看不见，这时让位给 :data:`MIN_DEPTH_LENGTH`。
        价格列不再占版面之后，够得到这个兜底的行必须窄到 60px 以下，而那时第 2 条
        早已把柱长压成 0 —— 兜底留着是防御性的，实际不会再触发。
        """
        share = float(max(0, int(self._config.theme2_depth_width or 0)))
        share = max(span * DEPTH_SHARE_MIN, min(share, span * DEPTH_SHARE_MAX))
        if span - share < MIN_KLINE_WIDTH:
            share = max(0.0, span - MIN_KLINE_WIDTH)
        mid_limit = max(0.0, abs(width / 2.0 - axis_x))
        return min(share, max(mid_limit, MIN_DEPTH_LENGTH))

    def _volume_span(self, chart_width: float) -> float:
        if chart_width <= 1:
            return 0.0
        return max(12.0, min(chart_width * 0.30, max(16.0, self._config.font_size * 3.2)))

    def _intraday_x(self, layout: _Layout, index: int, count: int) -> float:
        """分时序列下标 → 横坐标：横轴固定为一个完整交易日。

        分母取 ``max(SESSION_MINUTES, count - 1)``，一举满足两种情形：

        * 整天数据（>=241 点）铺满整条轴，末点落在 15:00 上；
        * 半天数据按「下标即开盘后的第几分钟」定位，11:30 恰好落在正中，
          曲线只画到当前时刻，不会把半天数据拉长成一天。

        午休被压缩，所以 11:30 与 13:00 是同一条线。
        """
        if count <= 1:
            return layout.plot_left
        denominator = max(SESSION_MINUTES, count - 1)
        fraction = min(max(index, 0), denominator) / denominator
        return layout.plot_left + layout.plot_span * fraction

    def _grid_xs(self, layout: _Layout) -> list[float]:
        """半小时刻度线的横坐标，含两端，共 ``240/30 + 1 = 9`` 条。"""
        if layout.plot_span < 2.0:
            return []
        steps = max(1, SESSION_MINUTES // GRID_STEP_MINUTES)
        return [
            layout.plot_left + layout.plot_span * index / steps
            for index in range(steps + 1)
        ]

    def _price_hit_rect(self) -> QRectF:
        """价格热区：从价格轴向行内延伸 ``price_width`` 的一条满高竖带。

        价格列本身已经不吃版面了（见 :meth:`_layout`），但「点价格展开/收起」这个
        手感要留住，于是热区改成贴着价格轴的那一条。``top()==0 /
        bottom()==height()`` 这条老契约原样保留——它保证点击判定与行高无关。
        """
        layout = self._layout()
        _, price_width, _ = self._metrics()
        left = layout.axis_x if layout.mirror else layout.axis_x - price_width
        return QRectF(left, 0.0, price_width, float(self.height()))

    def _price_click_target(self, position: QPointF) -> bool:
        """整列价格 + 居中后的那两块文字，都算「点了价格」。

        文字搬到虚线中点之后，热区不能只剩原来那一列——否则价格自己反而点不动。
        """
        if self._price_hit_rect().contains(position):
            return True
        price_rect, percent_rect = self._price_blocks(self._layout())
        return price_rect.contains(position) or percent_rect.contains(position)

    def _book_mid_price(self, levels) -> float | None:
        if self._price is not None and self._price > 0:
            return self._price
        bids = [row.price for row in levels if row.side == "bid" and row.price > 0]
        asks = [row.price for row in levels if row.side == "ask" and row.price > 0]
        if bids and asks:
            return (max(bids) + min(asks)) / 2.0
        return max(bids) if bids else min(asks) if asks else None

    # ------------------------------------------------------------ 绘制：盘口

    def _paint_color(self, normal: QColor, alpha: int | None = None) -> QColor:
        """本控件所有绘制颜色都从这里取。

        灰度显示时换成 ``grayscale_level`` 那**一个**灰阶——盘口买卖、B/S 标记、
        蜡烛、网格、轴线、角标与股价文字全部同色，深浅只由 alpha 区分；
        非灰度时原色照旧，alpha 一样可以就地覆盖。
        """
        config = self._config
        return display_color(
            normal,
            grayscale=config.grayscale,
            level=config.grayscale_level,
            alpha=alpha,
        )

    def _draw_depth(self, painter: QPainter, layout: _Layout) -> None:
        snapshot = self._depth
        if snapshot is None or not snapshot.available or not snapshot.levels:
            return

        levels = [row for row in snapshot.levels if row.price > 0 and row.volume > 0]
        if not levels:
            return
        mid_price = self._book_mid_price(levels)
        if mid_price is None or mid_price <= 0:
            return

        span = abs(layout.depth_edge - layout.axis_x)
        if span < 2:
            return

        center_y = self.height() / 2.0
        top = INNER_PAD
        bottom = float(self.height()) - INNER_PAD

        ask_by_price: dict[float, float] = {}
        bid_by_price: dict[float, float] = {}
        for row in levels:
            if row.side == "ask" and row.price >= mid_price:
                ask_by_price[row.price] = ask_by_price.get(row.price, 0.0) + row.volume
            elif row.side == "bid" and row.price <= mid_price:
                bid_by_price[row.price] = bid_by_price.get(row.price, 0.0) + row.volume

        # 主窗口按档位顺序平均占满上下半区，不按绝对价差压缩。
        asks = sorted(ask_by_price.items(), key=lambda item: item[0])  # 近 -> 远
        bids = sorted(bid_by_price.items(), key=lambda item: item[0], reverse=True)  # 近 -> 远

        def distributed_y(index: int, count: int, *, upper: bool) -> int:
            near = center_y - LEVEL_GAP if upper else center_y + LEVEL_GAP
            far = top if upper else bottom
            if count <= 1:
                return round((near + far) / 2.0)
            return round(near + (far - near) * index / (count - 1))

        buckets: dict[tuple[int, str], float] = {}
        for index, (_price, volume) in enumerate(asks):
            y = distributed_y(index, len(asks), upper=True)
            buckets[(y, "ask")] = buckets.get((y, "ask"), 0.0) + volume
        for index, (_price, volume) in enumerate(bids):
            y = distributed_y(index, len(bids), upper=False)
            buckets[(y, "bid")] = buckets.get((y, "bid"), 0.0) + volume
        if not buckets:
            return

        maximum = max(buckets.values()) or 1.0
        painter.setPen(QPen(self._paint_color(AXIS_COLOR, AXIS_ALPHA), 1.0))
        painter.drawLine(round(layout.axis_x), round(top), round(layout.axis_x), round(bottom))

        bid_color, ask_color = book_colors(self._config)
        for (y, side), volume in buckets.items():
            length = max(1.0, span * volume / maximum)
            # 彩色模式下买红卖绿（A 股），灰度模式下两者同灰、只剩上下半区的区别。
            is_bid = side == "bid"
            color = self._paint_color(bid_color if is_bid else ask_color, DEPTH_ALPHA)
            painter.setPen(QPen(color, 1.2))
            if layout.mirror:
                painter.drawLine(round(layout.axis_x), y, round(layout.axis_x + length), y)
            else:
                painter.drawLine(round(layout.axis_x - length), y, round(layout.axis_x), y)

    # ------------------------------------------------------------ 绘制：价格与虚线

    def _guide_span(self, layout: _Layout) -> tuple[float, float]:
        """当前价虚线的左右端点（先左后右）。

        虚线从价格列内侧一路指到对侧外沿，所以它的中点就是「行中间」——股价与
        涨跌幅要落到的位置。
        """
        if layout.mirror:
            return layout.price_right + 3.0, float(self.width()) - OUTER_PAD
        return OUTER_PAD, layout.price_left - 3.0

    def _price_font(self):
        return make_font(
            self._config,
            bold=self._config.stock_price_bold,
            pixel_size=max(10, self._config.stock_price_font_size),
        )

    def _percent_font(self):
        return make_font(
            self._config,
            bold=self._config.stock_percent_bold,
            pixel_size=max(8, self._config.stock_percent_font_size),
        )

    def _error_font(self):
        return make_font(
            self._config,
            bold=True,
            pixel_size=max(8, self._config.stock_percent_font_size),
        )

    def _price_gap(self) -> float:
        """股价下沿 / 涨跌幅上沿到虚线的间距。

        千档失败时中线那一行归红色 ``ERROR`` 角标，两块文字各自再让开半个角标高度，
        这样「股价永远在 ERROR 之上」是与角标高度无关的恒等式。
        """
        gap = PRICE_LINE_GAP
        if self._depth_error_active():
            gap += QFontMetricsF(self._error_font()).height() / 2.0
        return gap

    def _price_texts(self) -> tuple[str, str]:
        price = "--" if self._price is None else f"{self._price:.2f}"
        percent = (
            "--"
            if self._percent is None
            else f"{'+' if self._percent > 0 else ''}{self._percent:.2f}%"
        )
        return price, percent

    def _price_blocks(self, layout: _Layout) -> tuple[QRectF, QRectF]:
        """股价（上）与涨跌幅（下）两块文字——绘制与测试共用的**唯一真源**。

        水平中心取当前价虚线的中点（用户口径「显示到虚线中间」），一上一下夹住
        那条线；两块共用同一个宽度，所以左右边缘对齐、不再各贴一边。
        """
        left, right = self._guide_span(layout)
        center_x = (left + right) / 2.0
        center_y = self.height() / 2.0
        gap = self._price_gap()
        price_text, percent_text = self._price_texts()
        price_metrics = QFontMetricsF(self._price_font())
        percent_metrics = QFontMetricsF(self._percent_font())
        width = (
            max(
                price_metrics.horizontalAdvance(price_text),
                percent_metrics.horizontalAdvance(percent_text),
            )
            + PRICE_BLOCK_PAD * 2.0
        )
        price_height = max(20.0, price_metrics.height() + 2.0)
        percent_height = max(16.0, percent_metrics.height() + 2.0)
        return (
            QRectF(
                center_x - width / 2.0,
                center_y - gap - price_height,
                width,
                price_height,
            ),
            QRectF(center_x - width / 2.0, center_y + gap, width, percent_height),
        )

    def _draw_price_and_guide(self, painter: QPainter, layout: _Layout) -> None:
        """股价在虚线上方、涨跌幅在下方，两块都以虚线线段的中点为水平中心。

        中线那一行本身留给红色 ``ERROR`` 角标（千档失败时出现），所以价格恒在
        ``ERROR`` 之上，且两种状态之间不会因为文字换位而跳动。
        """
        config = self._config
        center_y = self.height() / 2.0
        price_font = self._price_font()
        price_text, percent_text = self._price_texts()
        price_rect, percent_rect = self._price_blocks(layout)
        guide_start, guide_end = self._guide_span(layout)
        align = Qt.AlignCenter

        # 当前价虚线：从价格列内侧一路指到对侧外沿，本身就是整行的水平中心。
        guide = self._paint_color(GUIDE_COLOR, GUIDE_ALPHA)
        if guide_end - guide_start > 2.0:
            # 关掉抗锯齿单独画这根线：1px 虚线落在半像素上会被均摊成两行 32% 的
            # 灰，看上去像被冲淡；这里让它正好压满一行像素。
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, False)
            pen = QPen(guide, 1.0, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            guide_y = round(center_y) + 0.5
            painter.drawLine(
                QPointF(round(guide_start), guide_y),
                QPointF(round(guide_end), guide_y),
            )
            painter.restore()

        painter.setBrush(guide)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(layout.axis_x - 1.8, center_y - 1.8, 3.6, 3.6))

        if self._depth_error_active():
            error_font = self._error_font()
            error_metrics = QFontMetricsF(error_font)
            error_width = error_metrics.horizontalAdvance("ERROR") + 6.0
            error_center = (guide_start + guide_end) / 2.0
            painter.setPen(self._paint_color(ERROR_COLOR))
            painter.setFont(error_font)
            painter.drawText(
                QRectF(
                    error_center - error_width / 2.0,
                    center_y - error_metrics.height() / 2.0,
                    error_width,
                    error_metrics.height(),
                ),
                Qt.AlignCenter,
                "ERROR",
            )

        # ``_price_rect`` 仍是满高的价格热区（与两块文字分开），绘制错误文案时
        # 借用中间那块文字位——价格列已经不占版面，没有别的落点了。
        self._price_rect = self._price_hit_rect()

        if self._error:
            painter.setPen(self._paint_color(MUTED))
            painter.setFont(
                make_font(config, pixel_size=max(8, config.stock_percent_font_size))
            )
            painter.drawText(price_rect, align, self._error[:18])
            return

        if not config.show_stock_price:
            # 设置页关掉「显示股价」时，两块文字都不画，虚线照画。
            return

        painter.setPen(self._paint_color(self._price_color))
        painter.setFont(price_font)
        painter.drawText(price_rect, align, price_text)

        painter.setPen(self._paint_color(self._percent_color))
        painter.setFont(self._percent_font())
        painter.drawText(percent_rect, align, percent_text)

    # ------------------------------------------------------------ 绘制：图表

    def _price_mapper(self, values: list[float]):
        """把价格映射到纵坐标；当前价恒定落在控件垂直中点上。"""
        top = INNER_PAD
        bottom = float(self.height()) - INNER_PAD
        inner = max(1.0, bottom - top)
        center_y = float(self.height()) / 2.0
        low, high = min(values), max(values)
        center = self._price if self._price and self._price > 0 else (low + high) / 2.0
        half = max(center - low, high - center)
        if half <= 0:
            half = max(abs(center) * 0.005, 0.01)

        def y_of(value: float) -> float:
            return center_y - (value - center) / (2.0 * half) * inner

        return y_of

    def _chart_series(self) -> tuple[list[float], list[float], list[float], list[Bar]]:
        """返回 (价格, 成交量, 量价聚合用的价位, 日K 原始柱)。"""
        if self._chart_mode == MODE_DAILY:
            bars = list(self._kline.bars) if self._kline else []
            prices = [bar.close for bar in bars]
            volumes = [max(0.0, bar.volume or 0.0) for bar in bars]
            levels = [(bar.high + bar.low) / 2.0 for bar in bars]
            return prices, volumes, levels, bars
        trend = self._trend
        prices = list(trend.prices) if trend else []
        volumes = [max(0.0, value or 0.0) for value in (trend.volumes if trend else [])]
        return prices, volumes, list(prices), []

    def _draw_volume(
        self,
        painter: QPainter,
        layout: _Layout,
        y_of=None,
        levels: list[float] | None = None,
        volumes: list[float] | None = None,
    ) -> None:
        """成交量柱贴「另一侧轴」镜像生长，轴本身照画，和价格轴对称。

        ``y_of`` 为空时只画轴——没有数据也要让「另一侧」这根轴在位。
        """
        if layout.volume_span <= 0:
            return

        painter.setPen(QPen(self._paint_color(AXIS_COLOR, AXIS_ALPHA), 1.0))
        painter.drawLine(
            round(layout.volume_axis),
            round(INNER_PAD),
            round(layout.volume_axis),
            round(float(self.height()) - INNER_PAD),
        )

        painter.setFont(self._popup_font())
        painter.setPen(self._paint_color(ANNOTATION_COLOR))
        label_width = max(0.0, layout.volume_span - 2)
        painter.drawText(
            QRectF(
                layout.volume_axis - layout.volume_span,
                INNER_PAD + 1,
                label_width,
                14,
            )
            if layout.mirror
            else QRectF(layout.volume_axis + 2, INNER_PAD + 1, label_width, 14),
            Qt.AlignRight | Qt.AlignTop if layout.mirror else Qt.AlignLeft | Qt.AlignTop,
            "量",
        )

        if y_of is None or not levels or not volumes:
            return
        buckets: dict[int, float] = {}
        for level, volume in zip(levels, volumes):
            if volume <= 0:
                continue
            row = round(y_of(level))
            if INNER_PAD <= row <= self.height() - INNER_PAD:
                buckets[row] = buckets.get(row, 0.0) + volume
        if not buckets:
            return

        maximum = max(buckets.values()) or 1.0
        color = self._paint_color(VOLUME_COLOR, 120)

        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for row, volume in buckets.items():
            length = max(1.0, layout.volume_span * volume / maximum)
            x0 = layout.volume_axis - length if layout.mirror else layout.volume_axis
            painter.fillRect(QRectF(x0, row - 1.0, length, 2.0), color)
        painter.restore()

    def _draw_time_grid(self, painter: QPainter, layout: _Layout) -> None:
        """分时的半小时竖虚线，纵向与两根竖轴等高。

        首末两条分别压在成交量轴与价格轴上，所以「9:30 对左轴、15:00 对右轴」
        是几何恒等式，不靠视觉对齐。日K 的横轴是日期，没有半小时可言，不画。
        """
        if self._chart_mode != MODE_INTRADAY:
            return
        xs = self._grid_xs(layout)
        if not xs:
            return

        color = self._paint_color(GRID_COLOR, GRID_ALPHA)

        painter.save()
        # 与当前价虚线同样关掉抗锯齿：1px 虚线落在半像素上会被均摊成两行浅灰。
        painter.setRenderHint(QPainter.Antialiasing, False)
        pen = QPen(color, 1.0, Qt.DashLine)
        pen.setDashPattern([3, 3])
        painter.setPen(pen)
        top_y = float(round(INNER_PAD))
        bottom_y = float(round(float(self.height()) - INNER_PAD))
        for x in xs:
            line_x = round(x) + 0.5
            painter.drawLine(QPointF(line_x, top_y), QPointF(line_x, bottom_y))
        painter.restore()

    def _draw_intraday(
        self, painter: QPainter, layout: _Layout, y_of, prices: list[float]
    ) -> None:
        count = len(prices)
        if layout.plot_span < 2 or count < 2:
            return

        # B/S 转折标记：B 从底部向上画、S 从顶部向下画，都停在曲线上，不穿过去。
        # 只有分时模式的横轴才是分钟索引，日K 的日期轴对不上这些索引。
        if self._show_signals and self._signals:
            top = INNER_PAD
            bottom = float(self.height()) - INNER_PAD
            for index, kind in self._signals:
                if not 0 <= index < count:
                    continue
                color = self._paint_color(BS_BUY_COLOR if kind.startswith("B") else BS_SELL_COLOR)
                painter.setPen(QPen(color, 1.2))
                x = self._intraday_x(layout, index, count)
                start = bottom if kind.startswith("B") else top
                painter.drawLine(QPointF(x, start), QPointF(x, y_of(prices[index])))

        curve = QPainterPath()
        curve.moveTo(QPointF(self._intraday_x(layout, 0, count), y_of(prices[0])))
        for index, value in enumerate(prices[1:], 1):
            curve.lineTo(
                QPointF(self._intraday_x(layout, index, count), y_of(value))
            )

        pen = QPen(self._paint_color(CURVE_COLOR), 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(curve)

    def _draw_candles(
        self, painter: QPainter, layout: _Layout, y_of, bars: list[Bar]
    ) -> None:
        count = len(bars)
        if layout.plot_span < 2 or count < 1:
            return
        step = layout.plot_span / count
        body_width = max(1.5, min(step * 0.62, 9.0))
        up = up_color(self._config)
        down = down_color(self._config)

        for index, bar in enumerate(bars):
            x = layout.plot_left + (index + 0.5) * step
            rising = bar.close >= bar.open
            color = self._paint_color(up if rising else down)
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(QPointF(x, y_of(bar.high)), QPointF(x, y_of(bar.low)))
            top = min(y_of(bar.open), y_of(bar.close))
            height = max(1.0, abs(y_of(bar.open) - y_of(bar.close)))
            painter.fillRect(QRectF(x - body_width / 2.0, top, body_width, height), color)

    def _draw_chart_badge(self, painter: QPainter, layout: _Layout) -> None:
        """分时/日K 角标；贴在图表区靠近盘口的一侧，避开外侧的「量」标注。"""
        label = MODE_LABELS.get(self._chart_mode, "")
        font = self._popup_font()
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(label) + 10.0
        height = metrics.height() + 2.0
        if layout.mirror:
            x = layout.kline_left + 1.0
        else:
            x = layout.kline_right - width - 1.0
        rect = QRectF(x, INNER_PAD + 1.0, width, height)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._paint_color(BADGE_BACKDROP, BADGE_BACKDROP.alpha()))
        painter.drawRoundedRect(rect, 3.0, 3.0)
        painter.setPen(self._paint_color(MUTED))
        painter.drawText(rect, Qt.AlignCenter, label)

    def _draw_chart_message(self, painter: QPainter, layout: _Layout, text: str) -> None:
        if not text:
            return
        painter.setFont(self._popup_font())
        painter.setPen(self._paint_color(MUTED))
        painter.drawText(
            QRectF(layout.kline_left, 0.0, max(0.0, layout.kline_right - layout.kline_left),
                   float(self.height())),
            Qt.AlignCenter,
            text,
        )

    def _chart_values(self) -> tuple[list[float], list[float], list[float], list[Bar], object]:
        """图表区一次绘制所需的全部数据，供「背景层 / 前景层」两次绘制共用。"""
        prices, volumes, levels, bars = self._chart_series()
        values = list(prices)
        if bars:
            values += [bar.high for bar in bars]
            values += [bar.low for bar in bars]
        if self._chart_mode == MODE_INTRADAY and self._prev_close:
            values.append(self._prev_close)
        y_of = self._price_mapper(values) if values else None
        return prices, volumes, levels, bars, y_of

    def _draw_chart_background(self, painter: QPainter, layout: _Layout) -> None:
        """K 线之下的一层：时间网格 + 成交量柱。"""
        self._draw_time_grid(painter, layout)
        if not layout.has_chart_band:
            return
        _prices, volumes, levels, _bars, y_of = self._chart_values()
        self._draw_volume(painter, layout, y_of, levels, volumes)

    def _draw_chart_foreground(self, painter: QPainter, layout: _Layout) -> None:
        """K 线之上的一层：曲线/蜡烛压在最上层。

        曲线现在横跨两根竖轴，会从盘口量条和成交量柱上方经过；先画它们、后画
        曲线，曲线才不会被那些 1px 量条切断。
        """
        if not layout.has_chart_band:
            return
        prices, _volumes, _levels, bars, y_of = self._chart_values()
        if y_of is None:
            message = self._kline_message or (
                "日K 加载中…" if self._chart_mode == MODE_DAILY else "暂无分时数据"
            )
            self._draw_chart_message(painter, layout, message)
        elif self._chart_mode == MODE_DAILY:
            self._draw_candles(painter, layout, y_of, bars)
        else:
            self._draw_intraday(painter, layout, y_of, prices)
        self._draw_chart_badge(painter, layout)

    # ------------------------------------------------------------ 绘制：入口

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        layout = self._layout()

        if self._expanded:
            self._chart_rect = QRectF(
                layout.chart_left,
                0.0,
                max(0.0, layout.chart_right - layout.chart_left),
                float(self.height()),
            )
            self._draw_chart_background(painter, layout)
        else:
            self._chart_rect = QRectF()

        self._draw_depth(painter, layout)

        if self._expanded:
            self._draw_chart_foreground(painter, layout)

        self._draw_price_and_guide(painter, layout)

    # ------------------------------------------------------------ 交互

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            position = event.position()
            if self._price_click_target(position):
                self.price_clicked.emit()
                event.accept()
                return
            if self._expanded and self._chart_rect.contains(position):
                self.toggle_chart_mode()
                event.accept()
                return
        super().mouseReleaseEvent(event)
