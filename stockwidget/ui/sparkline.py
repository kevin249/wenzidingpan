"""行内走势图：当日分时曲线 + 昨收虚线基准 + 面积填充。

优先画当日分时（联网取分钟数据），拿不到时回退成组件运行期间的采样点，
两种数据画法一致，区别只是点的来源。
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

SAMPLE_LEN = 40  # 回退模式下保留的采样点数
FILL_ALPHA = 38  # 面积填充的透明度，压得比曲线淡很多
BASELINE_ALPHA = 110


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._samples: deque[float] = deque(maxlen=SAMPLE_LEN)
        self._series: list[float] = []  # 当日分时，空则用采样点
        self._prev_close: float | None = None
        self._color = QColor(154, 163, 184)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

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

    def set_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def clear(self) -> None:
        self._samples.clear()
        self._series = []
        self.update()

    @property
    def points(self) -> list[float]:
        return self._series or list(self._samples)

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        points = self.points
        if len(points) < 2:
            return

        width = self.width() or 1
        height = self.height() or 1

        # 纵向范围要把昨收算进去，基准线才不会跑到图外。
        low, high = min(points), max(points)
        if self._prev_close is not None:
            low, high = min(low, self._prev_close), max(high, self._prev_close)
        span = (high - low) or max(abs(high) * 0.01, 0.01)

        def y_of(value: float) -> float:
            return (height - 2) - (value - low) / span * (height - 4)

        def x_of(index: int) -> float:
            return index / (len(points) - 1) * (width - 1)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        curve = QPainterPath()
        curve.moveTo(QPointF(x_of(0), y_of(points[0])))
        for index, value in enumerate(points[1:], 1):
            curve.lineTo(QPointF(x_of(index), y_of(value)))

        # 面积：曲线以下填到底，颜色跟涨跌走但压得很淡。
        area = QPainterPath(curve)
        area.lineTo(QPointF(x_of(len(points) - 1), height))
        area.lineTo(QPointF(x_of(0), height))
        area.closeSubpath()
        fill = QColor(self._color)
        fill.setAlpha(FILL_ALPHA)
        painter.fillPath(area, fill)

        # 昨收基准线
        if self._prev_close is not None:
            baseline = QColor(self._color)
            baseline.setAlpha(BASELINE_ALPHA)
            pen = QPen(baseline, 1, Qt.DashLine)
            pen.setDashPattern([4, 3])
            painter.setPen(pen)
            y = y_of(self._prev_close)
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        pen = QPen(self._color, 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(curve)
