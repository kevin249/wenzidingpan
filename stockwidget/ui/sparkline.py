"""行内迷你走势图：把最近若干个采样点画成一条折线。"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

HISTORY_LEN = 40


class Sparkline(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: deque[float] = deque(maxlen=HISTORY_LEN)
        self._color = QColor(154, 163, 184)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def push(self, value: float | None) -> None:
        if value is not None:
            self._points.append(value)
            self.update()

    def set_color(self, color: QColor) -> None:
        if color != self._color:
            self._color = color
            self.update()

    def clear(self) -> None:
        self._points.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if len(self._points) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        values = list(self._points)
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        width = self.width() or 1
        height = self.height() or 1

        path = QPainterPath()
        for index, value in enumerate(values):
            x = index / (len(values) - 1) * (width - 1)
            y = (height - 2) - (value - low) / span * (height - 3)
            point = QPointF(x, y + 1)
            path.moveTo(point) if index == 0 else path.lineTo(point)

        pen = QPen(self._color, 1.4)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
