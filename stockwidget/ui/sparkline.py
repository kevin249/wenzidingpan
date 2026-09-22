"""行内走势图：当日分时曲线 + 昨收虚线基准 + 可选面积填充。

优先画当日分时（联网取分钟数据），拿不到时回退成组件运行期间的采样点，
两种数据画法一致，区别只是点的来源。
"""

from __future__ import annotations

from collections import deque
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..mcp_depth import DEPTH_FIVE, DEPTH_FULL, DEPTH_TEN, DepthSnapshot

SAMPLE_LEN = 40  # 回退模式下保留的采样点数
FILL_ALPHA = 38  # 面积填充的透明度，压得比曲线淡很多
BASELINE_ALPHA = 110
BUY_COLOR = QColor(240, 79, 90)
SELL_COLOR = QColor(59, 130, 246)
DEPTH_BID_COLOR = QColor(240, 79, 90)
DEPTH_ASK_COLOR = QColor(34, 197, 94)
DEPTH_STALE_SECONDS = 120


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: deque[float] = deque(maxlen=SAMPLE_LEN)
        self._series: list[float] = []  # 当日分时，空则用采样点
        self._prev_close: float | None = None
        self._open_price: float | None = None
        self._signals: list[tuple[int, str]] = []
        self._show_signals = True
        self._show_open_line = True
        self._show_high_low = True
        self._show_fill = False
        self._grayscale = False
        self._color = QColor(154, 163, 184)
        self._preferred_height = 0
        self._annotation_font = QFont()
        self._annotation_font.setPixelSize(9)
        self._depth: DepthSnapshot | None = None
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
        return QSize(self.minimumWidth(), self._preferred_height)

    # ------------------------------------------------------------ 数据

    def push_sample(self, value: float | None) -> None:
        """记一个轮询采样点，只在没有分时数据时才会被画出来。"""
        if value is not None:
            self._samples.append(value)
            if not self._series:
                self.update()

    def set_series(self, prices: list[float]) -> None:
        if prices != self._series:
            self._series = list(prices)
            self.update()

    def set_prev_close(self, value: float | None) -> None:
        if value != self._prev_close:
            self._prev_close = value
            self.update()

    def set_depth(self, snapshot: DepthSnapshot | None) -> None:
        if snapshot != self._depth:
            self._depth = snapshot
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
    ) -> None:
        state = (
            open_price,
            signals,
            show_signals,
            show_open_line,
            show_high_low,
            show_fill,
            grayscale,
        )
        old = (self._open_price, self._signals, self._show_signals, self._show_open_line,
               self._show_high_low, self._show_fill, self._grayscale)
        if state != old:
            self._open_price = open_price
            self._signals = list(signals)
            self._show_signals = show_signals
            self._show_open_line = show_open_line
            self._show_high_low = show_high_low
            self._show_fill = show_fill
            self._grayscale = grayscale
            self.update()

    def set_annotation_options(
        self,
        *,
        show_signals: bool,
        show_open_line: bool,
        show_high_low: bool,
        show_fill: bool,
        grayscale: bool,
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
        )

    def clear(self) -> None:
        self._samples.clear()
        self._series = []
        self._signals = []
        self._open_price = None
        self._prev_close = None
        self.update()

    @property
    def points(self) -> list[float]:
        return self._series or list(self._samples)

    # ------------------------------------------------------------ 绘制

    def _draw_depth(self, painter: QPainter, width: int, height: int, y_of) -> None:
        snapshot = self._depth
        if snapshot is None or not snapshot.available or not snapshot.levels:
            return

        # 只画当前 K 线价格窗口内可见的挂单；千档价格绝不能反向撑大 K 线 Y 轴。
        buckets: dict[tuple[int, str], float] = {}
        for level in snapshot.levels:
            y = round(y_of(level.price))
            if y < 0 or y >= height:
                continue
            key = (y, level.side)
            buckets[key] = buckets.get(key, 0.0) + level.volume
        if not buckets:
            return

        maximum = max(buckets.values())
        if maximum <= 0:
            return
        max_width = min(240.0, max(24.0, width * 0.28))
        right = float(width - 1)

        painter.save()
        for (row, side), volume in buckets.items():
            bar_width = max(1.0, max_width * volume / maximum)
            if self._grayscale:
                color = QColor(155, 155, 155) if side == "bid" else QColor(105, 105, 105)
            else:
                color = QColor(DEPTH_BID_COLOR if side == "bid" else DEPTH_ASK_COLOR)
            color.setAlpha(88)
            painter.fillRect(QRectF(right - bar_width, row - 0.7, bar_width, 1.4), color)

        label = {
            DEPTH_FULL: "千档",
            DEPTH_TEN: "十档",
            DEPTH_FIVE: "五档",
        }.get(snapshot.depth_mode, "盘口")
        if snapshot.depth_mode != DEPTH_FULL:
            label += "·降级"
        if snapshot.received_at and time.time() - snapshot.received_at > DEPTH_STALE_SECONDS:
            label += "·延迟"
        label += f" {snapshot.bid_count}/{snapshot.ask_count}"
        painter.setFont(self._annotation_font)
        painter.setPen(QColor(145, 145, 145) if self._grayscale else QColor(185, 190, 202))
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
        low, high = price_low, price_high
        if self._prev_close is not None:
            low, high = min(low, self._prev_close), max(high, self._prev_close)
        if self._show_open_line and self._open_price is not None:
            low, high = min(low, self._open_price), max(high, self._open_price)
        span = (high - low) or max(abs(high) * 0.01, 0.01)

        def y_of(value: float) -> float:
            return (height - 2) - (value - low) / span * (height - 4)

        def x_of(index: int) -> float:
            return index / (len(points) - 1) * (width - 1)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # B/S 波动转折：B 红线从底部向上画到曲线，S 蓝线从顶部向下画到曲线，
        # 两者都停在转折点上，不穿过曲线。
        if self._show_signals:
            for index, kind in self._signals:
                if 0 <= index < len(points):
                    color = QColor(145, 145, 145) if self._grayscale else (
                        BUY_COLOR if kind == "B" else SELL_COLOR
                    )
                    painter.setPen(QPen(color, 1.2))
                    x = x_of(index)
                    y = y_of(points[index])
                    start = height - 1 if kind == "B" else 1
                    painter.drawLine(QPointF(x, start), QPointF(x, y))

        curve = QPainterPath()
        curve.moveTo(QPointF(x_of(0), y_of(points[0])))
        for index, value in enumerate(points[1:], 1):
            curve.lineTo(QPointF(x_of(index), y_of(value)))

        if self._show_fill:
            # 面积：曲线以下填到底，颜色跟涨跌走但压得很淡。
            area = QPainterPath(curve)
            area.lineTo(QPointF(x_of(len(points) - 1), height))
            area.lineTo(QPointF(x_of(0), height))
            area.closeSubpath()
            fill = QColor(self._color)
            fill.setAlpha(FILL_ALPHA)
            painter.fillPath(area, fill)

        # 千档挂单在 K 线内部右侧按相同价格轴叠加，曲线和基准线画在它上面。
        self._draw_depth(painter, width, height, y_of)

        # 昨收基准线
        if self._prev_close is not None:
            baseline = QColor(self._color)
            baseline.setAlpha(BASELINE_ALPHA)
            pen = QPen(baseline, 1, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            y = y_of(self._prev_close)
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        # 开盘价使用区别于昨收的点虚线。
        if self._show_open_line and self._open_price is not None:
            opening = QColor(145, 145, 145) if self._grayscale else QColor(245, 158, 11)
            opening.setAlpha(BASELINE_ALPHA)
            painter.setPen(QPen(opening, 1, Qt.DotLine))
            y = y_of(self._open_price)
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        pen = QPen(self._color, 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(curve)

        if self._show_high_low:
            painter.setFont(self._annotation_font)
            painter.setPen(QColor(150, 150, 150) if self._grayscale else self._color)
            painter.drawText(2, painter.fontMetrics().ascent() + 1, f"{price_high:.2f}")
            painter.drawText(2, height - 2, f"{price_low:.2f}")
