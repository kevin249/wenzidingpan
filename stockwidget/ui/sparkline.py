"""行内走势图：当日分时曲线 + 昨收虚线基准 + 可选面积填充。

优先画当日分时（联网取分钟数据），拿不到时回退成组件运行期间的采样点，
两种数据画法一致，区别只是点的来源。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..config import DEFAULT_GRAYSCALE_LEVEL
from ..local_bs import SAMPLE_LEN
from ..kline import Bar
from ..market_hours import SHANGHAI_TZ
from ..mcp_depth import DEPTH_FIVE, DEPTH_FULL, DEPTH_TEN, DepthSnapshot
from . import tdw_overlay
from .price_scale import PriceScale
from .sizing import SizedWidget
from .theme import display_color
from .time_axis import intraday_fraction

FILL_ALPHA = 38  # 面积填充的透明度，压得比曲线淡很多
BASELINE_ALPHA = 110
BUY_COLOR = QColor(240, 79, 90)
SELL_COLOR = QColor(59, 130, 246)
DEPTH_BID_COLOR = QColor(240, 79, 90)
DEPTH_ASK_COLOR = QColor(34, 197, 94)
TRADED_VOLUME_COLOR = QColor(148, 163, 184)
OPEN_LINE_COLOR = QColor(245, 158, 11)
ANNOTATION_COLOR = QColor(185, 190, 202)  # 「成交量 / 十档」这类小字标注
DEPTH_STALE_SECONDS = 120


class Sparkline(SizedWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: deque[float] = deque(maxlen=SAMPLE_LEN)
        self._sample_minutes: deque[str] = deque(maxlen=SAMPLE_LEN)
        self._series: list[float] = []  # 当日分时，空则用采样点
        self._minutes: list[str] = []
        self._realtime_bars: list[Bar] = []
        self._fill_width = False
        self._volumes: list[float] = []
        self._show_volume_profile = False
        self._configured_profile_width = 0
        self._prev_close: float | None = None
        self._open_price: float | None = None
        self._tdw_power: list[float | None] = []
        self._show_tdw_levels = False
        self._signals: list[tuple[int, str]] = []
        self._show_signals = True
        self._show_open_line = True
        self._show_high_low = True
        self._show_fill = False
        self._grayscale = False
        self._grayscale_level = DEFAULT_GRAYSCALE_LEVEL
        self._color = QColor(154, 163, 184)
        self._preferred_height = 0
        self._annotation_font = QFont()
        self._annotation_font.setPixelSize(9)
        self._depth: DepthSnapshot | None = None
        self._show_depth = True
        self._price_scale = PriceScale()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    # ------------------------------------------------------------ 尺寸

    def set_preferred_height(self, height: int) -> None:
        """曲线本身没有固有尺寸，想要多高由外层告诉它。

        高度必须走 sizeHint 而不是最小高度：最小高度会一路顶成窗口的硬下限，
        行数一多就把后面的股票顶到屏幕外，而这个组件刻意不带滚动条。
        """
        if height != self._preferred_height:
            self._preferred_height = height
            self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self._display_size_hint(QSize(self.minimumWidth(), self._preferred_height))

    # ------------------------------------------------------------ 数据

    def push_sample(self, value: float | None) -> None:
        """记一个轮询采样点，只在没有分时数据时才会被画出来。"""
        if value is not None:
            self._samples.append(value)
            self._sample_minutes.append(datetime.now(SHANGHAI_TZ).strftime("%H:%M"))
            if not self._series:
                self.update()

    def set_series(self, prices: list[float], minutes: list[str] | None = None,
                   realtime_bars: list[Bar] | None = None) -> None:
        resolved_minutes = list(minutes or [])
        bars = realtime_bars or []
        if prices != self._series or resolved_minutes != self._minutes or bars != self._realtime_bars:
            self._realtime_bars = bars
            self._series = list(prices)
            self._minutes = resolved_minutes
            self.update()

    def set_fill_width(self, enabled: bool) -> None:
        """关闭时右端固定 15:00；开启时按已有数据的时间范围铺满。"""
        if enabled != self._fill_width:
            self._fill_width = enabled
            self.update()

    def set_volume_profile(self, volumes: list[float], *, enabled: bool = True) -> None:
        """设置按分钟成交量；启用后按成交价聚合到左侧价格-成交量分布。"""
        cleaned = [max(0.0, float(value or 0.0)) for value in volumes]
        state = (cleaned, bool(enabled))
        old = (self._volumes, self._show_volume_profile)
        if state != old:
            self._volumes = cleaned
            self._show_volume_profile = bool(enabled)
            self.update()

    def set_side_profile_width(self, width: int) -> None:
        """设置左右成交量/挂单侧栏的共同宽度；0 表示按组件宽度自动计算。"""
        value = max(0, int(width or 0))
        if value != self._configured_profile_width:
            self._configured_profile_width = value
            self.update()

    def set_prev_close(self, value: float | None) -> None:
        if value != self._prev_close:
            self._prev_close = value
            self.update()

    def set_depth(self, snapshot: DepthSnapshot | None) -> None:
        if snapshot != self._depth:
            self._depth = snapshot
            self.update()

    def set_tdw_power(self, power, *, show_levels=True):
        if (power, show_levels) != (self._tdw_power, self._show_tdw_levels):
            self._tdw_power, self._show_tdw_levels = power, show_levels
            self.update()

    def set_show_depth(self, enabled: bool) -> None:
        """只切换挂单叠加层，保留曲线、尺寸和最近收到的深度数据。"""
        if self._show_depth != enabled:
            self._show_depth = enabled
            self.update()

    def set_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def set_annotation_font(self, font: QFont) -> None:
        """设置最高价/最低价字体，窗口缩放时由外层按比例更新。"""
        if font != self._annotation_font:
            self._annotation_font = QFont(font)
            self.update()

    def set_annotations(
        self,
        open_price: float | None,
        signals: list[tuple[int, str]],
        *,
        show_signals: bool,
        show_open_line: bool,
        show_high_low: bool,
        show_fill: bool,
        grayscale: bool,
        grayscale_level: int = DEFAULT_GRAYSCALE_LEVEL,
    ) -> None:
        state = (
            open_price,
            signals,
            show_signals,
            show_open_line,
            show_high_low,
            show_fill,
            grayscale,
            grayscale_level,
        )
        old = (self._open_price, self._signals, self._show_signals, self._show_open_line,
               self._show_high_low, self._show_fill, self._grayscale,
               self._grayscale_level)
        if state != old:
            self._open_price = open_price
            self._signals = list(signals)
            self._show_signals = show_signals
            self._show_open_line = show_open_line
            self._show_high_low = show_high_low
            self._show_fill = show_fill
            self._grayscale = grayscale
            self._grayscale_level = grayscale_level
            self.update()

    def set_annotation_options(
        self,
        *,
        show_signals: bool,
        show_open_line: bool,
        show_high_low: bool,
        show_fill: bool,
        grayscale: bool,
        grayscale_level: int = DEFAULT_GRAYSCALE_LEVEL,
    ) -> None:
        """只更新显示开关，供 WebUI 配置即时生效，不必等待下一次行情。"""
        self.set_annotations(
            self._open_price,
            self._signals,
            show_signals=show_signals,
            show_open_line=show_open_line,
            show_high_low=show_high_low,
            show_fill=show_fill,
            grayscale=grayscale,
            grayscale_level=grayscale_level,
        )

    def clear(self, *, preserve_scale: bool = False) -> None:
        if not preserve_scale:
            self._price_scale.reset()
        self._samples.clear()
        self._sample_minutes.clear()
        self._series = []
        self._minutes = []
        self._realtime_bars = []
        self._volumes = []
        self._show_volume_profile = False
        self._signals = []
        self._tdw_power = []
        self._open_price = None
        self._prev_close = None
        self.update()

    @property
    def points(self) -> list[float]:
        return self._series or list(self._samples)

    # ------------------------------------------------------------ 绘制

    def _paint_color(self, normal: QColor, alpha: int | None = None) -> QColor:
        """本控件所有绘制颜色都从这里取：灰度模式统一成一个灰阶，层次只靠 alpha。"""
        return display_color(
            normal,
            grayscale=self._grayscale,
            level=self._grayscale_level,
            alpha=alpha,
        )

    def _side_profile_width(self, width: int) -> float:
        """成交量与挂单使用完全相同的左右侧栏宽度。"""
        automatic = min(240.0, max(24.0, width * 0.28))
        target = float(self._configured_profile_width) if self._configured_profile_width > 0 else automatic
        # 至少给中间 K 线保留 80px，窗口太窄时两侧等比例收紧。
        maximum = max(24.0, (width - 80.0) / 2.0)
        return min(target, maximum)

    def _draw_traded_volume(
        self,
        painter: QPainter,
        height: int,
        y_of,
        profile_width: float,
    ) -> None:
        if not self._show_volume_profile or not self._series or not self._volumes:
            return

        buckets: dict[int, float] = {}
        for price, volume in zip(self._series, self._volumes):
            if volume <= 0:
                continue
            row = round(y_of(price))
            if 0 <= row < height:
                buckets[row] = buckets.get(row, 0.0) + volume
        if not buckets:
            return

        maximum = max(buckets.values()) or 1.0
        color = self._paint_color(TRADED_VOLUME_COLOR, 82)

        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for row, volume in buckets.items():
            bar_width = max(1.0, profile_width * volume / maximum)
            painter.fillRect(QRectF(0.0, row - 0.7, bar_width, 1.4), color)

        painter.setFont(self._annotation_font)
        painter.setPen(self._paint_color(ANNOTATION_COLOR))
        painter.drawText(
            QRectF(2, 1, max(0.0, profile_width - 4), max(12, height / 4)),
            Qt.AlignLeft | Qt.AlignTop,
            "成交量",
        )
        painter.restore()

    def _draw_depth(
        self,
        painter: QPainter,
        width: int,
        height: int,
        y_of,
        profile_width: float,
    ) -> None:
        snapshot = self._depth
        if snapshot is None or not snapshot.available or not snapshot.levels:
            return

        # 只画当前 K 线价格窗口内可见的挂单；千档价格绝不能反向撑大 K 线 Y 轴。
        buckets: dict[tuple[int, str], float] = {}
        best_ask, best_bid = float("inf"), 0.0
        for level in snapshot.levels:
            if level.price <= 0 or level.volume <= 0:
                continue
            if level.side == "ask":
                best_ask = min(best_ask, level.price)
            elif level.side == "bid":
                best_bid = max(best_bid, level.price)
            else:
                continue
            y = round(y_of(level.price))
            if 0 <= y < height:
                key = (y, level.side)
                buckets[key] = buckets.get(key, 0.0) + level.volume
        if not buckets:
            return

        mid = None
        spread_pixels = 0.0
        if 0 < best_bid < best_ask < float("inf"):
            ask_y, bid_y = y_of(best_ask), y_of(best_bid)
            mid = round((ask_y + bid_y) / 2)
            spread_pixels = bid_y - ask_y
        occupied_rows = {y for y, side in buckets}
        dense = len(occupied_rows) > height / 2
        if dense:
            # One 2 px bar + 1 px of space per price band. Never pool bid and ask.
            bands = {}
            for (y, side), volume in buckets.items():
                if mid is None:
                    center = 3 * (y // 3) + 1
                else:
                    offset = 2 + 3 * (max(0, abs(y - mid) - 1) // 3)
                    center = mid - offset if side == "ask" else mid + offset
                key = (max(0, min(height - 1, center)), side)
                bands[key] = bands.get(key, 0.0) + volume
            buckets = bands
        maximum = max(buckets.values())
        max_width = min(profile_width, width)
        right = float(width - 1)
        # Draw the book with solid pixels; antialiased translucent rows merge into the backdrop.
        alpha = 255 if self._grayscale else 215
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, False)
        for (row, side), volume in buckets.items():
            bar_width = max(1.0, max_width * volume / maximum)
            # Use two pixels for isolated levels and keep dense price rows distinct.
            thickness = 1 if not dense and (row - 1 in occupied_rows or row + 1 in occupied_rows) else 2
            top = row - thickness // 2
            if (row, "ask") in buckets and (row, "bid") in buckets:
                # Split a shared screen row around its center so both sides survive.
                thickness = 1
                top = row - 1 if side == "ask" else row
            color = self._paint_color(
                DEPTH_BID_COLOR if side == "bid" else DEPTH_ASK_COLOR, alpha
            )
            painter.fillRect(QRectF(right - bar_width, top, bar_width, thickness), color)

        if mid is not None and 0 <= mid < height:
            left = max(0.0, right - max_width)
            ink = self._paint_color(ANNOTATION_COLOR)
            if dense or spread_pixels >= 4:
                painter.setPen(QPen(ink, 1, Qt.DotLine))
                painter.drawLine(QPointF(left, mid), QPointF(right, mid))
            # End markers remain readable when a one-tick spread is subpixel.
            painter.setPen(Qt.NoPen)
            painter.setBrush(ink)
            for x, dx in ((left, -4.0), (right - 4.0, 4.0)):
                painter.drawPolygon(QPolygonF([QPointF(x, mid), QPointF(x + dx, mid - 3),
                                               QPointF(x + dx, mid + 3)]))
            if max_width >= 56 and height >= 32:
                painter.setFont(self._annotation_font)
                painter.setPen(ink)
                h = painter.fontMetrics().height()
                for top, word in ((mid - h - 2, "卖"), (mid + 2, "买")):
                    if 0 <= top and top + h <= height:
                        painter.drawText(QRectF(max(0, left - 18), top, 16, h), Qt.AlignCenter, word)

        label = {
            DEPTH_FULL: "千档",
            DEPTH_TEN: "十档",
            DEPTH_FIVE: "五档",
        }.get(snapshot.depth_mode, "盘口")
        if snapshot.depth_mode != DEPTH_FULL:
            label += "·降级"
        if snapshot.received_at and time.time() - snapshot.received_at > DEPTH_STALE_SECONDS:
            label += "·延迟"
        label += "·合量" if dense else ""
        if max_width >= 80:
            label += f" {snapshot.bid_count}/{snapshot.ask_count}"
        painter.setFont(self._annotation_font)
        label_color = self._paint_color(ANNOTATION_COLOR)
        backing = 15 if label_color.lightness() >= 120 else 245
        painter.setBackground(QBrush(QColor(backing, backing, backing, 210)))
        painter.setBackgroundMode(Qt.OpaqueMode)
        painter.setPen(label_color)
        painter.drawText(
            QRectF(max(0.0, width - max_width - 2), 1, max_width, max(12, height / 4)),
            Qt.AlignRight | Qt.AlignTop,
            label,
        )
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        points = self.points
        if len(points) < 2:
            return

        width = self.width() or 1
        height = self.height() or 1

        # 纵向范围要把昨收算进去，基准线才不会跑到图外。
        price_low, price_high = min(points), max(points)
        if self._realtime_bars:
            price_low = min(price_low, min(bar.low for bar in self._realtime_bars))
            price_high = max(price_high, max(bar.high for bar in self._realtime_bars))
        low, high = price_low, price_high
        if self._prev_close is not None:
            low, high = min(low, self._prev_close), max(high, self._prev_close)
        if self._show_open_line and self._open_price is not None:
            low, high = min(low, self._open_price), max(high, self._open_price)
        low, high = self._price_scale.bounds(low, high)
        span = high - low

        def y_of(value: float) -> float:
            return (height - 2) - (value - low) / span * (height - 4)

        profile_width = self._side_profile_width(width) if self._show_volume_profile else 0.0
        chart_left = profile_width + 2.0 if self._show_volume_profile else 0.0
        chart_right = (
            max(chart_left + 1.0, width - profile_width - 3.0)
            if self._show_volume_profile
            else float(width - 1)
        )

        minutes = self._minutes if self._series else list(self._sample_minutes)

        def x_of(index: int) -> float:
            fraction = intraday_fraction(
                index, len(points), minutes, fill_width=self._fill_width
            )
            return chart_left + fraction * (chart_right - chart_left)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 曲线色（＝设置里的股价涨跌色）在这里过一次灰度：外部就算塞了彩色进来，
        # 灰度模式下画出来也只有一个灰。
        curve_color = self._paint_color(self._color)

        # B/S 波动转折：B 红线从底部向上画到曲线，S 蓝线从顶部向下画到曲线，
        # 两者都停在转折点上，不穿过曲线。
        if self._show_signals and not self._tdw_power:
            for index, kind in self._signals:
                if 0 <= index < len(points):
                    color = self._paint_color(BUY_COLOR if kind.startswith("B") else SELL_COLOR)
                    painter.setPen(QPen(color, 1.2))
                    x = x_of(index)
                    y = y_of(points[index])
                    start = height - 1 if kind.startswith("B") else 1
                    painter.drawLine(QPointF(x, start), QPointF(x, y))

        curve = QPainterPath()
        curve.moveTo(QPointF(x_of(0), y_of(points[0])))
        prefix = len(points) - len(self._realtime_bars)
        for index, value in enumerate(points[1:], 1):
            curve.lineTo(QPointF(x_of(index), y_of(value)))

        if self._show_fill:
            # 面积：曲线以下填到底，颜色跟涨跌走但压得很淡。
            area = QPainterPath(curve)
            area.lineTo(QPointF(x_of(len(points) - 1), height))
            area.lineTo(QPointF(x_of(0), height))
            area.closeSubpath()
            painter.fillPath(area, self._paint_color(self._color, FILL_ALPHA))

        # 主题2展开图：左侧历史成交量分布，右侧实时挂单分布，二者等宽；K线居中。
        if self._show_volume_profile:
            self._draw_traded_volume(painter, height, y_of, profile_width)
        if self._show_depth:
            self._draw_depth(painter, width, height, y_of, profile_width or self._side_profile_width(width))

        level_labels = None
        if self._show_signals and self._show_tdw_levels:
            guide_right = chart_right
            if self._show_depth and self._depth and self._depth.available:
                guide_right = min(guide_right, width - self._side_profile_width(width) - 6)
            label_inset = painter.fontMetrics().horizontalAdvance(f"{price_high:.2f}") + 6 if self._show_high_low else 0
            level_labels = tdw_overlay.draw_levels(painter, chart_left, guide_right, height,
                                                  self._paint_color(tdw_overlay.LEVEL_COLOR, 190),
                                                  label_inset=label_inset)

        # 昨收基准线
        if self._prev_close is not None:
            pen = QPen(self._paint_color(self._color, BASELINE_ALPHA), 1, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            y = y_of(self._prev_close)
            painter.drawLine(QPointF(chart_left, y), QPointF(chart_right, y))

        # 开盘价使用区别于昨收的点虚线。
        if self._show_open_line and self._open_price is not None:
            painter.setPen(
                QPen(self._paint_color(OPEN_LINE_COLOR, BASELINE_ALPHA), 1, Qt.DotLine)
            )
            y = y_of(self._open_price)
            painter.drawLine(QPointF(chart_left, y), QPointF(chart_right, y))

        if self._show_signals and self._tdw_power:
            tdw_overlay.draw_power(painter, self._tdw_power, x_of, height,
                                   self._paint_color(tdw_overlay.POWER_COLOR, 180))
        if self._grayscale and self._show_depth and self._depth and self._depth.available:
            painter.save()
            book_width = profile_width or self._side_profile_width(width)
            painter.setClipRect(QRectF(width - book_width - 2, 0, book_width + 2, height))
            backing = 15 if curve_color.lightness() >= 120 else 245
            painter.setPen(QPen(QColor(backing, backing, backing, 230), 3.4))
            painter.drawPath(curve)
            painter.restore()
        pen = QPen(curve_color, 1 if self._realtime_bars else 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(curve)
        tdw_overlay.draw_realtime_candles(painter, self._realtime_bars, prefix, x_of, y_of, curve_color)

        if self._show_signals and self._tdw_power:
            labels = []
            for index, kind in self._signals:
                if 0 <= index < len(points):
                    color = self._paint_color(BUY_COLOR if kind.startswith("B") else SELL_COLOR)
                    tdw_overlay.draw_event(painter, x_of(index), y_of(points[index]), kind, height, width, color, labels)

        tdw_overlay.draw_level_labels(painter, level_labels)

        if self._show_high_low:
            painter.setFont(self._annotation_font)
            painter.setPen(curve_color)
            label_x = round(chart_left + 2)
            painter.drawText(label_x, painter.fontMetrics().ascent() + 1, f"{price_high:.2f}")
            painter.drawText(label_x, height - 2, f"{price_low:.2f}")
