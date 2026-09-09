"""托盘图标：用 QPainter 现画，省掉一个二进制资源文件。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

DEFAULT_TRAY_COLOR = "#ffffff"
DEFAULT_TRAY_ALERT_COLOR = "#3b82f6"


def tray_icon(size: int = 64, alert: bool = False, normal_color: str = DEFAULT_TRAY_COLOR, alert_color: str = DEFAULT_TRAY_ALERT_COLOR) -> QIcon:
    color = QColor(alert_color if alert else normal_color)
    if not color.isValid():
        color = QColor(DEFAULT_TRAY_ALERT_COLOR if alert else DEFAULT_TRAY_COLOR)

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.NoBrush)

    pen = QPen(color, size * 0.08)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    margin = size * 0.08
    painter.drawRoundedRect(QRectF(margin, margin, size - margin * 2, size - margin * 2), size * 0.18, size * 0.18)

    path = QPainterPath()
    points = [(0.22, 0.72), (0.41, 0.50), (0.56, 0.62), (0.78, 0.28)]
    path.moveTo(QPointF(points[0][0] * size, points[0][1] * size))
    for x, y in points[1:]:
        path.lineTo(QPointF(x * size, y * size))
    painter.drawPath(path)

    arrow = QPainterPath()
    arrow.moveTo(QPointF(0.60 * size, 0.28 * size))
    arrow.lineTo(QPointF(0.78 * size, 0.28 * size))
    arrow.lineTo(QPointF(0.78 * size, 0.46 * size))
    painter.drawPath(arrow)
    painter.end()
    return QIcon(pixmap)
