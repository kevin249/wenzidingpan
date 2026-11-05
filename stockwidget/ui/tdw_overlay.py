"""Draw TDW on an independent 0–4 scale and mark events at their price bars."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF


POWER_COLOR = QColor(232, 188, 74)


def draw_realtime_candles(painter, bars, offset, x_of, y_of, color):
    """Group only for painting when several real-time bars share four screen pixels."""
    if not bars:
        return
    painter.save()
    painter.setPen(QPen(color, 1))
    i = 0
    while i < len(bars):
        x = x_of(offset + i)
        first = bars[i]
        high, low, close = first.high, first.low, first.close
        i += 1
        while i < len(bars) and x_of(offset + i) < x + 4:
            bar = bars[i]
            high, low, close = max(high, bar.high), min(low, bar.low), bar.close
            i += 1
        center = (x + x_of(offset + i - 1)) / 2
        painter.drawLine(QPointF(center, y_of(high)), QPointF(center, y_of(low)))
        top, bottom = sorted((y_of(first.open), y_of(close)))
        if bottom - top < 1:
            painter.drawLine(QPointF(center - 1, top), QPointF(center + 1, top))
        else:
            painter.setBrush(Qt.NoBrush if close > first.open else color)
            painter.drawRect(QRectF(center - 1, top, 2, bottom - top))
    painter.restore()


def draw_power(painter, power, x_of, height, color):
    path = QPainterPath()
    contiguous = False
    for index, value in enumerate(power):
        if value is None:
            contiguous = False
            continue
        xy = QPointF(x_of(index), 2 + (1 - value / 4) * max(1, height - 4))
        if contiguous:
            path.lineTo(xy)
        else:
            path.moveTo(xy)
        contiguous = True
    painter.save()
    painter.setPen(QPen(color, 1, Qt.DashLine))
    painter.drawPath(path)
    if power and height >= 55 and x_of(len(power) - 1) - x_of(0) >= 240:
        latest = next((value for value in reversed(power) if value is not None), None)
        if latest is not None:
            font = painter.font()
            font.setPixelSize(10)
            painter.setFont(font)
            painter.drawText(QPointF(x_of(0) + 52, height - 3), f"TDW {latest:.2f}")
    painter.restore()


def draw_event(painter, x, y, text, height, width, color, labels):
    buying = text.startswith("B")
    y = max(1.0, min(height - 1.0, y))
    # Preserve triangle direction at frame edges and connect back to the price.
    delta = 5 if buying else -5
    tip = min(y, height - 6) if buying else max(6, y)
    base = tip + delta
    painter.save()
    forming = text.endswith("*")
    painter.setPen(QPen(color, 1) if forming else Qt.NoPen)
    painter.setBrush(Qt.NoBrush if forming else color)
    painter.drawPolygon(QPolygonF([QPointF(x, tip), QPointF(x - 3, base), QPointF(x + 3, base)]))
    if tip != y:
        painter.setPen(color)
        painter.drawLine(QPointF(x, tip), QPointF(x, y))
    if height >= 38 and width >= 240:
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)
        text_width = painter.fontMetrics().horizontalAdvance(text) + 3
        left = max(0, min(width - text_width, x - text_width / 2))
        top = max(0, min(height - 13, base if delta > 0 else base - 13))
        rect = QRectF(left, top, text_width, 13)
        if not any(rect.intersects(other) for other in labels):
            painter.drawText(rect, Qt.AlignCenter, text)
            labels.append(rect)
    painter.restore()
