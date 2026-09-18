"""托盘图标：用 QPainter 现画，省掉一个二进制资源文件。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

DEFAULT_TRAY_COLOR = "#ffffff"
DEFAULT_TRAY_ALERT_COLOR = "#3b82f6"


def unread_badge_text(count: int) -> str:
    """托盘数字角标：0 不显示，超过两位数时用 99+ 保证小图标仍可辨认。"""
    count = max(0, int(count))
    if count == 0:
        return ""
    return str(count) if count <= 99 else "99+"


def _contrast_text_color(background: QColor) -> QColor:
    """为用户自定义的提醒色挑一个足够醒目的黑/白前景。"""
    luminance = 0.299 * background.red() + 0.587 * background.green() + 0.114 * background.blue()
    return QColor("#111827" if luminance >= 170 else "#ffffff")


def tray_icon(
    size: int = 64,
    alert: bool = False,
    normal_color: str = DEFAULT_TRAY_COLOR,
    alert_color: str = DEFAULT_TRAY_ALERT_COLOR,
    unread: int = 0,
    unread_font_size: int = 14,
) -> QIcon:
    unread = max(0, int(unread))
    unread_font_size = max(7, min(32, int(round(unread_font_size))))
    alert = bool(alert or unread)

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
    painter.drawRoundedRect(
        QRectF(margin, margin, size - margin * 2, size - margin * 2),
        size * 0.18,
        size * 0.18,
    )

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

    label = unread_badge_text(unread)
    if label:
        # Windows 通知区最终通常只显示 16~24 px，角标必须占到接近半个图标才看得清。
        badge = QRectF(size * 0.42, size * 0.50, size * 0.55, size * 0.45)
        badge_color = QColor(alert_color)
        if not badge_color.isValid():
            badge_color = QColor(DEFAULT_TRAY_ALERT_COLOR)
        text_color = _contrast_text_color(badge_color)

        painter.setPen(QPen(text_color, max(1.0, size * 0.025)))
        painter.setBrush(badge_color)
        painter.drawRoundedRect(badge, size * 0.14, size * 0.14)

        font = painter.font()
        font.setBold(True)
        label_font_size = unread_font_size if len(label) <= 2 else round(unread_font_size * 0.78)
        font.setPixelSize(max(6, label_font_size))
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(badge, Qt.AlignCenter, label)

    painter.end()
    return QIcon(pixmap)
