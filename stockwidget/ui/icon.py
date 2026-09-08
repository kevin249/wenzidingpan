"""托盘图标：用 QPainter 现画，省掉一个二进制资源文件。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap

from .theme import GREEN, RED


def tray_icon(size: int = 64, alert: bool = False) -> QIcon:
    """绿色圆角块 + 白色上行折线，浅色和深色任务栏上都看得清。

    ``alert`` 是有未读提醒时的样子：整块换成红色。通知区里图标只有 16px 左右，
    在这个尺寸上「整体换色」是唯一一眼能看出来的差别——画角标数字只会糊成一团，
    未读条数放在鼠标悬停的 tooltip 里。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(RED if alert else GREEN)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    path = QPainterPath()
    points = [(0.22, 0.72), (0.41, 0.50), (0.56, 0.62), (0.78, 0.28)]
    path.moveTo(QPointF(points[0][0] * size, points[0][1] * size))
    for x, y in points[1:]:
        path.lineTo(QPointF(x * size, y * size))

    pen = QPen(Qt.white, size * 0.09)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawPath(path)

    # 箭头
    arrow = QPainterPath()
    arrow.moveTo(QPointF(0.60 * size, 0.28 * size))
    arrow.lineTo(QPointF(0.78 * size, 0.28 * size))
    arrow.lineTo(QPointF(0.78 * size, 0.46 * size))
    painter.drawPath(arrow)
    painter.end()

    return QIcon(pixmap)
