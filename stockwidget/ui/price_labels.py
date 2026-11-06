"""Legible high/low labels shared by all chart themes."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF


def draw_price_labels(painter, rect: QRectF, low: float, high: float, font, color):
    """Use device font metrics and explicit baselines to avoid clipping glyphs."""
    area = rect.adjusted(2, 2, -2, -2)
    if area.width() <= 0 or area.height() <= 0:
        return
    labels = (f"高 {high:.2f}", f"低 {low:.2f}")
    font = QFont(font)
    font.setBold(True)
    metrics = QFontMetricsF(font, painter.device())
    size = font.pixelSize() if font.pixelSize() > 0 else QFontInfo(font).pixelSize()
    while size > 1:
        fit = min(1.0, area.width() / max(metrics.horizontalAdvance(text) for text in labels),
                  area.height() / (2 * metrics.height()))
        if fit >= 1:
            break
        size = max(1, min(size - 1, int(size * fit)))
        font.setPixelSize(size)
        metrics = QFontMetricsF(font, painter.device())
    painter.save()
    painter.setBackgroundMode(Qt.TransparentMode)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(QPointF(area.left(), area.top() + metrics.ascent()), labels[0])
    painter.drawText(QPointF(area.right() - metrics.horizontalAdvance(labels[1]),
                            area.bottom() - metrics.descent()), labels[1])
    painter.restore()
