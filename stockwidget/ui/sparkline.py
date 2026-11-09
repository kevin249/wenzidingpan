"""行内走势图：当日分时曲线 + 昨收虚线基准 + 可选面积填充。

优先画当日分时（联网取分钟数据），拿不到时回退成组件运行期间的采样点，
两种数据画法一致，区别只是点的来源。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..config import DEFAULT_GRAYSCALE_LEVEL
from ..local_bs import SAMPLE_LEN
from ..kline import Bar
from ..market_hours import SHANGHAI_TZ
from ..mcp_depth import DepthSnapshot, depth_is_stale, depth_time_label
from . import tdw_overlay
from .price_scale import PriceScale
from .price_labels import draw_price_labels
from .depth_layout import depth_rows
from .sizing import SizedWidget
from .theme import display_color, with_opacity
from .time_axis import average_points, SESSION_MINUTES, intraday_fraction, trading_minute

FILL_ALPHA = 38  # 面积填充的透明度，压得比曲线淡很多
BASELINE_ALPHA = 110
BUY_COLOR = QColor(240, 79, 90)
SELL_COLOR = QColor(59, 130, 246)
DEPTH_BID_COLOR = QColor(240, 79, 90)
DEPTH_ASK_COLOR = QColor(34, 197, 94)
TRADED_VOLUME_COLOR = QColor(148, 163, 184)
OPEN_LINE_COLOR = QColor(245, 158, 11)
ANNOTATION_COLOR = QColor(185, 190, 202)  # 「成交量 / 十档」这类小字标注


class Sparkline(SizedWidget):
    depth_invalidated = Signal()
    depth_painted = Signal(object, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: deque[float] = deque(maxlen=SAMPLE_LEN)
        self._sample_minutes: deque[str] = deque(maxlen=SAMPLE_LEN)
        self._series: list[float] = []  # 当日分时，空则用采样点
        self._minutes: list[str] = []
        self._card_style = False
        self._show_card_tdw = True
        self._text_opacities = {}
        self._depth_width = 0
        self._split_depth = False
        self._chart_line_width = 1.4
        self._chart_line_color = QColor("#000000")
        self._average_line_color = QColor("#ffff00")
        self._average_line_width = 1.5
        self._depth_line_width = .8
        self._tdw_label_color = QColor("#f4f4f1")
        self._book_scheme = "cn"
        self._averages: list[tuple[str, float]] = []
        self._realtime_bars: list[Bar] = []
        self._fill_width = False
        self._recent_view = False
        self._volumes: list[float] = []
        self._show_volume_profile = False
        self._configured_profile_width = 0
        self._prev_close: float | None = None
        self._open_price: float | None = None
        self._tdw_power: list[float | None] = []
        self._tdw_events: list[tuple[int, str]] = []
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
        self._live_price: float | None = None
        self._live_price_font = QFont()
        self._live_price_color = QColor("#f4f4f1")
        self._show_live_price = False
        self._depth: DepthSnapshot | None = None
        self._changed_depth_keys: set[tuple[str, float]] = set()
        self._show_depth = True
        self._depth_expired = False
        self._last_depth_paint = None
        self._depth_clock = QTimer(self)
        self._depth_clock.setInterval(1000)
        self._depth_clock.timeout.connect(self._check_depth_age)
        self._price_scale = PriceScale()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    # ------------------------------------------------------------ 尺寸

    def set_display_options(self, *, show_tdw=True, depth_width=0, text_opacities=None,
                            chart_line_width=1.4, depth_line_width=.8,
                            chart_line_color="#000000", average_line_color="#ffff00", average_line_width=1.5):
        self._show_card_tdw = show_tdw
        self._depth_width = depth_width
        self._text_opacities = dict(text_opacities or {})
        self._chart_line_width = chart_line_width
        self._chart_line_color = QColor(chart_line_color)
        self._average_line_color = QColor(average_line_color)
        self._average_line_width = average_line_width
        self._depth_line_width = depth_line_width
        self.update()

    def set_split_depth(self, enabled: bool) -> None:
        if self._split_depth != enabled:
            self._split_depth = enabled
            self.update()

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
                   realtime_bars: list[Bar] | None = None, *, recent=False, averages=None) -> None:
        resolved_minutes = list(minutes or [])
        bars = realtime_bars or []
        averages = averages or []
        if (prices != self._series or resolved_minutes != self._minutes or bars != self._realtime_bars
                or averages != self._averages or recent != self._recent_view):
            self._recent_view = recent
            self._realtime_bars = bars
            self._series = list(prices)
            self._minutes = resolved_minutes
            self._averages = averages
            self.update()

    def set_card_style(self, enabled: bool, label_color="#f4f4f1", scheme="cn") -> None:
        state = (enabled, QColor(label_color), scheme)
        if state != (self._card_style, self._tdw_label_color, self._book_scheme):
            self._card_style, self._tdw_label_color, self._book_scheme = state
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
            previous = self._depth
            if snapshot is not None and previous is not None and previous.symbol == snapshot.symbol:
                old = {(level.side, level.price): level.volume for level in previous.levels}
                new = {(level.side, level.price): level.volume for level in snapshot.levels}
                self._changed_depth_keys = {key for key in old.keys() | new.keys()
                                            if old.get(key) != new.get(key)}
            else:
                self._changed_depth_keys = set()
            self._depth = snapshot
            if snapshot is not None:
                self._depth_clock.start()
                self.setToolTip(f"L2 {depth_time_label(snapshot)}\n{snapshot.reason}")
            else:
                self._depth_clock.stop()
                self.setToolTip("")
            self._check_depth_age()
            self.update()
            self.depth_invalidated.emit()

    def _check_depth_age(self) -> None:
        expired = bool(self._depth and depth_is_stale(self._depth))
        if expired != self._depth_expired:
            self._depth_expired = expired
            self.update()
            self.depth_invalidated.emit()

    def set_tdw_power(self, power, *, show_levels=True, events=()):
        if (power, show_levels, events) != (self._tdw_power, self._show_tdw_levels, self._tdw_events):
            self._tdw_power, self._show_tdw_levels, self._tdw_events = power, show_levels, list(events)
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

    def set_live_price(self, price: float | None, font: QFont, color: QColor, *, visible: bool) -> None:
        state = (price, font, color, visible)
        if state != (self._live_price, self._live_price_font, self._live_price_color,
                     self._show_live_price):
            self._live_price = price
            self._live_price_font = QFont(font)
            self._live_price_color = QColor(color)
            self._show_live_price = visible
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
        self._averages = []
        self._recent_view = False
        self._minutes = []
        self._realtime_bars = []
        self._volumes = []
        self._show_volume_profile = False
        self._signals = []
        self._tdw_power = []
        self._tdw_events = []
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
        if self._card_style:
            return min(self._depth_width or width * .55, width * .65)
        automatic = min(240.0, max(24.0, width * 0.28))
        target = float(self._configured_profile_width) if self._configured_profile_width > 0 else automatic
        # 至少给中间 K 线保留 80px，窗口太窄时两侧等比例收紧。
        maximum = max(24.0, (width - 80.0) / 2.0)
        return min(target, maximum)

    def _book_width(self, width, chart_width=None):
        if self._card_style:
            # The limit is relative to the actual candle plot, excluding axes.
            span = max(0.0, width - 1 if chart_width is None else chart_width)
            return min(self._depth_width or span * .5, span * .5)
        return min(self._depth_width, max(0, width * .65)) if self._depth_width else self._side_profile_width(width)

    def _text_color(self, normal, region="chart", alpha=None):
        return with_opacity(self._paint_color(normal, alpha), self._text_opacities.get(region, 1.0))

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
        painter.setPen(self._text_color(ANNOTATION_COLOR))
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
        if snapshot is None:
            return
        if not snapshot.available or not snapshot.levels or depth_is_stale(snapshot):
            painter.save()
            painter.setFont(self._annotation_font)
            painter.setPen(self._text_color(ANNOTATION_COLOR))
            label = "L2 过期·等待新数据" if depth_is_stale(snapshot) or "source_stale" in snapshot.reason else "L2 暂无新数据"
            painter.drawText(QRectF(0, 1, width, height), Qt.AlignRight | Qt.AlignTop, label)
            painter.restore()
            self._record_depth_paint(snapshot, False)
            return

        # Theme 3 shares the K-line price axis. Drawing levels by ordinal rank
        # here would put a different price on the same right-hand price tick.
        pad = max(.5, self._depth_line_width / 2)
        label_height = QFontMetrics(self._annotation_font).height()
        if not (self._card_style and y_of is not None) and (self._show_high_low or self._card_style) and height >= 4 * label_height:
            pad = max(pad, label_height * (2 if self._card_style and width >= 300 else 1) + 1)
        pad = min(pad, max(0.0, (height - 1) / 2))
        if self._card_style and y_of is not None:
            rows = [(y, level) for level in snapshot.levels
                    if math.isfinite(y := y_of(level.price)) and 0 <= y < height]
        else:
            rows = depth_rows(snapshot.levels, pad, height - pad, center_gap=.5)

        maximum = max((level.volume for _, level in rows), default=0)
        max_width = max(0.0, min(profile_width, width))
        if max_width <= 0:
            return
        right = float(width - 1)
        if rows:
            painter.save()
            painter.setClipRect(QRectF(max(0, right - max_width), 0, max_width + 1, height), Qt.IntersectClip)
            painter.setRenderHint(QPainter.Antialiasing, True)
            # Each visible price keeps its own Y and a common linear width scale.
            pixels_per_unit = max_width / maximum
            for y, level in sorted(rows, key=lambda item: (
                    (item[1].side, item[1].price) in self._changed_depth_keys,
                    -item[1].volume)):
                bar_width = pixels_per_unit * level.volume
                red_side = "ask" if self._card_style and self._book_scheme == "us" else "bid"
                changed = (level.side, level.price) in self._changed_depth_keys
                color = self._paint_color(DEPTH_BID_COLOR if level.side == red_side else DEPTH_ASK_COLOR,
                                          255 if changed or self._grayscale else 215)
                painter.setPen(QPen(color, max(self._depth_line_width, 1.25) if changed else
                                    self._depth_line_width, Qt.SolidLine, Qt.FlatCap))
                painter.drawLine(QPointF(right - bar_width, y), QPointF(right, y))

            if not self._card_style and {level.side for _, level in rows} == {"ask", "bid"}:
                mid = height / 2
                painter.setPen(QPen(self._paint_color(ANNOTATION_COLOR), 1, Qt.DotLine))
                painter.drawLine(QPointF(max(0, right - max_width), mid), QPointF(right, mid))
            painter.restore()
        painter.save()
        if self._card_style:
            painter.setFont(self._annotation_font)
            painter.setPen(self._text_color(ANNOTATION_COLOR))
            caption = depth_time_label(snapshot).rsplit(" ", 1)[-1]
            label_width = min(width, max(max_width, QFontMetrics(self._annotation_font).horizontalAdvance(caption) + 4))
            painter.drawText(QRectF(width - label_width, 1, label_width, height),
                             Qt.AlignRight | Qt.AlignTop, caption)
        painter.restore()
        self._record_depth_paint(snapshot, bool(rows))

    def _record_depth_paint(self, snapshot, displayed):
        # Emit once per delivered frame, after drawing, never on mere receipt.
        state = (snapshot, displayed)
        if state != self._last_depth_paint:
            self._last_depth_paint = state
            self.depth_painted.emit(snapshot, displayed)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        points = self.points
        if not points:
            indicator_height = (min(self.height() // 2, max(8, round(self.height() * .28)))
                                if self._card_style and self._show_card_tdw else 0)
            with QPainter(self) as painter:
                if self._show_depth and not self._split_depth:
                    self._draw_depth(painter, self.width(), self.height() - indicator_height,
                                     None, self._book_width(self.width()))
                elif self._split_depth:
                    painter.setPen(self._text_color(ANNOTATION_COLOR))
                    painter.drawText(self.rect(), Qt.AlignCenter, "等待分时价格轴")
                if indicator_height:
                    painter.translate(0, self.height() - indicator_height)
                    painter.setFont(self._annotation_font)
                    tdw_overlay.draw_power_panel(painter, [], lambda _: 0,
                                                 self.width() - 1, self.width(), indicator_height,
                                                 self._paint_color, self._text_color(self._tdw_label_color, "tdw"))
            return

        width = self.width() or 1
        total_height = self.height() or 1
        metrics = QFontMetrics(self._annotation_font)
        axes = self._card_style and width >= 150 and total_height >= max(60, metrics.height() * 4 + 16)
        axis_width = 0
        if axes:
            axis_width = metrics.horizontalAdvance(f"{max(points):.2f}") + 6
            width -= axis_width
        tdw_enabled = self._show_signals and self._show_tdw_levels
        # 大卡片给动力线独立的纵轴，小卡片保留原有叠加刻度。
        if self._card_style:
            indicator_height = min(total_height // 2, max(8, round(total_height * .28))) if self._show_card_tdw else 0
        else:
            indicator_height = min(44, total_height // 3) if tdw_enabled and total_height >= 72 else 0
        height = total_height - indicator_height

        # Only plotted prices and candle wicks determine the price window.
        price_low, price_high = min(points), max(points)
        if self._realtime_bars:
            price_low = min(price_low, min(bar.low for bar in self._realtime_bars))
            price_high = max(price_high, max(bar.high for bar in self._realtime_bars))
        if self._card_style and self._show_live_price and self._live_price is not None:
            price_low = min(price_low, self._live_price)
            price_high = max(price_high, self._live_price)
        scale = PriceScale() if self._recent_view else self._price_scale
        low, high = scale.bounds(price_low, price_high)
        span = high - low
        label_height = QFontMetrics(self._annotation_font).height()
        price_inset = label_height if self._show_high_low and height >= 4 * label_height else 0

        def y_of(value: float) -> float:
            return (height - price_inset - 2) - (value - low) / span * (height - 2 * price_inset - 4)

        live_y = (y_of(self._live_price) if self._card_style and self._show_live_price
                  and self._live_price is not None else None)

        profile_width = self._side_profile_width(width) if self._show_volume_profile else 0.0
        chart_left = profile_width + 2.0 if self._show_volume_profile else 0.0
        has_book = self._show_depth and self._depth and self._depth.available and self._depth.levels
        book_width = self._book_width(width) if has_book else profile_width
        chart_right = float(width - 1)
        split = self._split_depth and axes
        panel_width = max(0, round(width * .32)) if split else 0
        axis_x = width
        panel_left = width
        if split:
            chart_right = max(chart_left + 1.0, width - panel_width - 2.0)
            axis_x = chart_right + 2.0
            panel_left = axis_x + axis_width
        if book_width and not self._card_style:
            chart_right = max(chart_left + 1.0, width - book_width - 6)

        minutes = self._minutes if self._series else list(self._sample_minutes)

        def x_of(index: int) -> float:
            fraction = intraday_fraction(
                index, len(points), minutes, fill_width=self._fill_width or self._recent_view
            )
            return chart_left + fraction * (chart_right - chart_left)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 曲线使用独立颜色；开启灰度时仍统一为用户指定的灰阶。
        curve_color = self._paint_color(self._chart_line_color)
        if axes:
            painter.setFont(self._annotation_font)
            grid_color = self._paint_color(self._color, 65)
            # 使用 1 / 2 / 5 的十进制价格间隔，轴标直接对应盘口价格。
            if self._card_style:
                # More price range deserves more readable price labels, up to
                # what the available rows can hold without overlap.
                plot_height = max(1, height - 2 * price_inset - 4)
                wanted = max(3, 4 + int(4 * math.log2(1 + span)))
                target = max(span / (3 * wanted), span * max(8, label_height * .68) / plot_height)
            else:
                target = span / max(2, min(8, height // (label_height + 8)))
            unit = 10 ** math.floor(math.log10(target))
            factors = (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if self._card_style else (1, 2, 2.5, 5, 10)
            step = next(f * unit for f in factors if f * unit >= target)
            for tick in range(math.ceil(low / step), math.floor(high / step) + 1):
                price = tick * step
                y = y_of(price)
                if y < label_height / 2 or y > height - label_height / 2:
                    continue
                painter.setPen(QPen(grid_color, .8, Qt.DashLine))
                painter.drawLine(QPointF(chart_left, y), QPointF(self.width() - 1 if split else width, y))
                painter.setPen(self._text_color(self._color))
                painter.drawText(QRectF(axis_x + 2, y - label_height / 2,
                                        axis_width - 2 if split else self.width() - width - 2,
                                        label_height), Qt.AlignLeft | Qt.AlignVCenter, f"{price:.2f}")

            start, end = 0.0, float(SESSION_MINUTES)
            ticks = [(0, "09:30"), (0.5, "11:30/13:00"), (1, "15:00")]
            if chart_right >= metrics.horizontalAdvance("11:30/13:00") * 5:
                ticks[1:2] = [(.25, "10:30"), ticks[1], (.75, "14:00")]
            if self._fill_width or self._recent_view:
                first = minutes[0] if minutes else ""
                last = minutes[-1] if minutes else ""
                start, end = trading_minute(first), trading_minute(last)
                ticks = [(0, first), (1, last)] if start is not None and end is not None and end > start else []
            for fraction, _caption in ticks:
                x = chart_left + fraction * (chart_right - chart_left)
                painter.setPen(QPen(grid_color, .8, Qt.DashLine))
                painter.drawLine(QPointF(x, 1), QPointF(x, height - 1))
                if indicator_height > label_height + 15:
                    painter.drawLine(QPointF(x, height + label_height + 5), QPointF(x, total_height - 7))
        average_path = QPainterPath()
        for fraction, price in average_points(self._averages, minutes,
                                              fill_width=self._fill_width or self._recent_view):
            x = chart_left + fraction * (chart_right - chart_left)
            if not average_path.elementCount():
                average_path.moveTo(x, y_of(price))
            else:
                average_path.lineTo(x, y_of(price))
        painter.save()
        painter.setClipRect(QRectF(chart_left, 0, chart_right - chart_left + 1, height))
        painter.setPen(QPen(self._paint_color(self._average_line_color), self._average_line_width,
                            Qt.DashLine if self._grayscale else Qt.SolidLine))
        painter.drawPath(average_path)
        painter.restore()

        # B/S 波动转折：B 红线从底部向上画到曲线，S 蓝线从顶部向下画到曲线，
        # 两者都停在转折点上，不穿过曲线。
        if not self._card_style and self._show_signals and not self._tdw_power:
            for index, kind in self._signals:
                if 0 <= index < len(points):
                    color = self._paint_color(BUY_COLOR if kind.startswith("B") else SELL_COLOR)
                    painter.setPen(QPen(color, 1.2))
                    x = x_of(index)
                    y = y_of(points[index])
                    start = height - 1 if kind.startswith("B") else 1
                    painter.drawLine(QPointF(x, start), QPointF(x, y))

        prefix = len(points) - len(self._realtime_bars)
        spacing = 1 if self._card_style else 4
        curve = tdw_overlay.series_path(points, x_of, y_of, spacing if self._realtime_bars else 0)

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
            if split:
                painter.save()
                painter.translate(panel_left, 0)
                self._draw_depth(painter, max(1, self.width() - panel_left), height, y_of,
                                 self._book_width(self.width() - panel_left, chart_right - chart_left))
                painter.restore()
            else:
                self._draw_depth(painter, width, height, y_of, self._book_width(width, chart_right - chart_left))

        if tdw_enabled and not self._card_style:
            painter.save()
            if indicator_height:
                painter.translate(0, height)
            lane_height = indicator_height or height
            tdw_overlay.draw_levels(painter, chart_left, chart_right, lane_height,
                                    self._paint_color(tdw_overlay.LEVEL_COLOR, 85), captions=False)
            painter.restore()

        # 昨收基准线
        if self._prev_close is not None:
            pen = QPen(self._paint_color(self._color, BASELINE_ALPHA), 1, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            y = y_of(self._prev_close)
            if 0 <= y < height:
                painter.drawLine(QPointF(chart_left, y), QPointF(chart_right, y))

        # 开盘价使用区别于昨收的点虚线。
        if self._show_open_line and self._open_price is not None:
            painter.setPen(
                QPen(self._paint_color(OPEN_LINE_COLOR, BASELINE_ALPHA), 1, Qt.DotLine)
            )
            y = y_of(self._open_price)
            if 0 <= y < height:
                painter.drawLine(QPointF(chart_left, y), QPointF(chart_right, y))

        if not self._card_style and self._show_signals and self._tdw_power:
            painter.save()
            if indicator_height:
                painter.translate(0, height)
            tdw_overlay.draw_power(painter, self._tdw_power, x_of, indicator_height or height,
                                   self._paint_color(tdw_overlay.POWER_COLOR, 155),
                                   text_opacity=self._text_opacities.get("tdw", 1.0))
            painter.restore()
        pen = QPen(curve_color, self._chart_line_width)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(curve)
        if not self._card_style:
            tdw_overlay.draw_realtime_candles(painter, self._realtime_bars, prefix, x_of, y_of, curve_color,
                                               line_width=self._chart_line_width)

        if not self._card_style and self._show_signals and self._tdw_power:
            for index, kind in tdw_overlay.visible_events(self._signals, len(points), x_of):
                color = self._paint_color(BUY_COLOR if kind.startswith("B") else SELL_COLOR)
                tdw_overlay.draw_event(painter, x_of(index), y_of(points[index]), kind, height,
                                       width, color, None)

        guide_rows = {}
        if self._card_style and indicator_height:
            painter.save()
            painter.translate(0, height)
            painter.setFont(self._annotation_font)
            guide_rows = tdw_overlay.draw_power_panel(
                painter, self._tdw_power, x_of,
                chart_right, self.width(), indicator_height,
                self._paint_color, self._text_color(self._tdw_label_color, "tdw"),
            )
            painter.restore()

        if self._show_signals and (guide_rows or not self._card_style):
            power_top = height if indicator_height else 0
            tags = tdw_overlay.MARKER_TAGS if self._card_style else tdw_overlay.EXIT_TAGS
            for index, value, tag in tdw_overlay.connector_points(
                self._tdw_events, self._tdw_power, len(points), tags=tags
            ):
                x, price_y = x_of(index), y_of(points[index])
                source_y = guide_rows[tag] if guide_rows else tdw_overlay.power_y(value, indicator_height or height)
                tdw_overlay.draw_tdw_exit(painter, x, price_y, power_top + source_y, self._paint_color)

        if live_y is not None:
            ink = self._paint_color(self._live_price_color, self._live_price_color.alpha())
            painter.setPen(QPen(ink, 1, Qt.DashLine))
            painter.drawLine(QPointF(chart_left, live_y), QPointF(self.width() - 1 if split else width, live_y))
        if self._show_high_low:
            draw_price_labels(painter, QRectF(chart_left, 0,
                              (chart_right if split else width) - chart_left, height),
                              price_low, price_high, self._annotation_font,
                              self._text_color(self._color, "high_low"), card=self._card_style)
