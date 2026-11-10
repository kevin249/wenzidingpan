"""Draw TDW on an independent 0–4 scale and mark events at their price bars."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from ..tdw import LEVELS

POWER_COLOR = QColor(232, 188, 74)
LEVEL_COLOR = QColor(185, 196, 208)
EXIT_TAGS = ("S2", "S1")
MARKER_TAGS = ("S2", "B1")


def power_y(value, height):
    return 2 + (1 - value / 4) * max(1, height - 4)


def draw_levels(painter, left, right, height, color, *, label_inset=0, mirror=False, captions=True):
    """Draw guide strokes beneath the data and return the captions for the foreground."""
    if right - left < 2 or height < 4:
        return None
    painter.save()
    painter.setClipRect(QRectF(left, 0, right - left, height), Qt.IntersectClip)
    painter.setRenderHint(QPainter.Antialiasing, False)
    font = painter.font()
    font.setPixelSize(10 if height >= 38 else 9)
    font.setBold(True)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    label_width = max(metrics.horizontalAdvance(f"{tag} {v:g}") for tag, v in LEVELS) + 6
    label_height = metrics.height()
    show_labels = captions and height >= 2 * label_height and right - left - label_inset >= 2 * label_width + 18

    # At tiny widget heights a pair can round to the same pixel.
    rows = {tag: round(power_y(value, height)) for tag, value in LEVELS}
    rows["S2"] = max(0, min(rows["S2"], rows["S1"] - 1))
    rows["B1"] = min(height - 1, max(rows["B1"], rows["B2"] + 1))
    labels = []
    for tag, value in LEVELS:
        outer = tag in ("S2", "B1")
        pen = QPen(color, 1, Qt.CustomDashLine, Qt.FlatCap)
        pen.setDashPattern([8, 4] if outer else [2, 3])
        painter.setPen(pen)
        y = rows[tag]
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        if show_labels:
            # Use separate columns for each pair, away from the order book.
            inset = label_inset + (0 if outer else label_width + 4)
            x = right - label_width - inset if mirror else left + inset
            top = max(0, min(y - label_height / 2, height - label_height))
            labels.append((QRectF(x, top, label_width, label_height), f"{tag} {value:g}"))
    painter.restore()
    return (font, color, labels) if labels else None


def draw_level_labels(painter, captions, *, text_opacity=1.0):
    if captions is None:
        return
    font, color, labels = captions
    painter.save()
    painter.setOpacity(painter.opacity() * text_opacity)
    ink = QColor(color)
    ink.setAlpha(255)
    backing = QColor(18, 18, 18, 242) if ink.lightness() >= 120 else QColor(246, 246, 246, 242)
    painter.setPen(ink)
    painter.setFont(font)
    for rect, caption in labels:
        painter.fillRect(rect, backing)
        painter.drawText(rect, Qt.AlignCenter, caption)
    painter.restore()


def draw_realtime_candles(painter, bars, offset, x_of, y_of, color, *, line_width=1):
    """Group only for painting when several real-time bars share four screen pixels."""
    if not bars:
        return
    painter.save()
    painter.setPen(QPen(color, line_width))
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


def series_path(values, x_of, y_of, spacing=0):
    """Preserve gaps and endpoints; paint the last real value in each crowded x interval."""
    path = QPainterPath()
    bucket_x = None
    last_x, last_value = 0, None
    for i, value in enumerate(values):
        if value is None:
            if last_value is not None:
                path.lineTo(last_x, y_of(last_value))
            bucket_x = last_value = None
            continue
        x = x_of(i)
        if bucket_x is None:
            path.moveTo(x, y_of(value))
            bucket_x = x
        elif x - bucket_x >= spacing:
            path.lineTo(last_x, y_of(last_value))
            bucket_x = x
        last_x, last_value = x, value
    if last_value is not None:
        path.lineTo(last_x, y_of(last_value))
    return path


def visible_events(events, count, x_of):
    """Give the most recent signal priority if several triangles would overlap."""
    retained = []
    right = float("inf")
    for index, kind in reversed(events):
        if 0 <= index < count:
            x = x_of(index)
            if right - x >= 12:
                retained.append((index, kind))
                right = x
    return reversed(retained)


def visible_power_points(power, x_of):
    """Keep each screen column's first, extrema and last sample in time order.

    Transaction bars can be much denser than pixels. Collapsing a column to
    only its last sample hides crossings; connecting every second obscures the
    curve. A missing sample still breaks the trace.
    """
    column = None
    first = low = high = last = None

    def flush():
        if first is not None:
            for point in sorted({p[0]: p for p in (first, low, high, last)}.values()):
                yield point[1], point[2]

    for index, value in enumerate(power):
        if value is None:
            yield from flush()
            first = low = high = last = None
            column = None
            yield None
            continue
        x = x_of(index)
        pixel = math.floor(x)
        if column is not None and pixel != column:
            yield from flush()
            first = low = high = last = None
        point = (index, x, value)
        if first is None:
            first = low = high = point
        if value < low[2]:
            low = point
        if value > high[2]:
            high = point
        last = point
        column = pixel
    yield from flush()


def draw_power(painter, power, x_of, height, color, *, text_opacity=1.0):
    span = x_of(len(power) - 1) - x_of(0) if power else 0
    spacing = 9 if len(power) > max(1, span) else 0
    path = series_path(power, x_of, lambda value: power_y(value, height), spacing)
    painter.save()
    painter.setPen(QPen(color, 1, Qt.DashLine))
    painter.drawPath(path)
    if power and height >= 55 and x_of(len(power) - 1) - x_of(0) >= 240:
        latest = next((value for value in reversed(power) if value is not None), None)
        if latest is not None:
            font = painter.font()
            font.setPixelSize(10)
            painter.setFont(font)
            painter.setOpacity(painter.opacity() * text_opacity)
            painter.drawText(QPointF(x_of(0) + 132, height - 3), f"TDW {latest:.2f}")
    painter.restore()


def draw_power_panel(painter, power, x_of, right, width, height, paint_color, text_color):
    """绘制独立的动力轴，返回参考线实际绘制的像素行。"""
    painter.save()
    painter.setClipRect(QRectF(0, 0, width, height), Qt.IntersectClip)
    metrics = painter.fontMetrics()
    caption_h = metrics.height()
    latest = next((v for v in reversed(power) if v is not None), None)
    painter.setPen(QPen(paint_color(LEVEL_COLOR, 85), 1))
    painter.drawLine(QPointF(0, 0), QPointF(width, 0))
    painter.setPen(text_color)
    if height < 4 or right < 1:
        painter.restore()
        return {}
    # Guides take priority over captions in a compact panel.
    caption = f"TDW  {latest:.2f}" if latest is not None else "TDW  等待成交数据"
    show_caption = height >= caption_h + 36 and width >= metrics.horizontalAdvance(caption)
    if show_caption:
        painter.drawText(QRectF(0, 1, width, caption_h), Qt.AlignLeft | Qt.AlignVCenter, caption)
    top, bottom = (caption_h + 4 if show_caption else 0), height - 1

    def y_of(value):
        return bottom - (bottom - top) * value / 4

    guides = {"S2": "#bf65b5", "S1": "#2fa9b1", "B2": "#d2ae38", "B1": "#71a75d"}
    # Recompute the 0–4 mapping for every paint. Distinct source levels retain
    # distinct rows even when rounding would collapse the upper/lower pairs.
    ordered = sorted(LEVELS, key=lambda item: item[1], reverse=True)
    previous_row = int(top) - 1
    guide_rows = {}
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, False)
    for index, (tag, value) in enumerate(ordered):
        row = max(previous_row + 1, min(round(y_of(value)), int(bottom) - (3 - index)))
        previous_row = row
        guide_rows[tag] = row
        painter.setPen(QPen(paint_color(QColor(guides[tag]), 220), 1))
        painter.drawLine(QPointF(0, row), QPointF(right, row))
    painter.restore()
    if latest is not None and height >= 80:
        painter.setPen(QPen(paint_color(QColor("#83c95d"), 195), 1, Qt.DotLine))
        painter.drawLine(QPointF(0, y_of(latest)), QPointF(right, y_of(latest)))

    red, green = paint_color(QColor("#f2615d")), paint_color(QColor("#50d185"))
    previous = None
    for point in visible_power_points(power, x_of):
        if point is None:
            previous = None
            continue
        x, value = point
        if previous is not None:
            old_x, old_value = previous
            painter.setPen(QPen(red if value >= old_value else green, 1.3))
            painter.drawLine(QPointF(old_x, y_of(old_value)), QPointF(x, y_of(value)))
        else:
            painter.setPen(QPen(red, 1.3))
            painter.drawPoint(QPointF(x, y_of(value)))
        previous = (x, value)
    painter.restore()
    return guide_rows


def exit_kinds(text):
    """从同一根 K 线的本地 TDW 事件中取出逃顶和清仓。"""
    tags = text.rstrip("*").split("/")
    return [tag for tag in EXIT_TAGS if tag in tags]


def connector_points(events, power, count, *, tags=MARKER_TAGS):
    for index, kind in events:
        if 0 <= index < min(count, len(power)) and power[index] is not None:
            codes = kind.rstrip("*").split("/")
            tag = next((tag for tag in tags if tag in codes), None)
            if tag:
                yield index, power[index], tag


def draw_tdw_exit(painter, x, price_y, tdw_y, paint_color):
    """在同一时间坐标上从动力线向上指到价格。"""
    if tdw_y <= price_y:
        return
    head_depth = min(5.0, tdw_y - price_y)
    head = QPainterPath(QPointF(x - 3.5, price_y + head_depth))
    head.lineTo(QPointF(x, price_y))
    head.lineTo(QPointF(x + 3.5, price_y + head_depth))
    ink = paint_color(QColor("#7d8998"))
    pen = QPen(ink, 1, Qt.CustomDashLine, Qt.FlatCap)
    pen.setDashPattern([5, 3])
    painter.save()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(pen)
    painter.drawLine(QPointF(x, tdw_y), QPointF(x, price_y))
    painter.setPen(QPen(ink, 1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPath(head)
    painter.restore()


def draw_event(painter, x, y, text, height, width, color, labels, *, text_opacity=1.0):
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
    if labels is not None and height >= 38 and width >= 240:
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
            painter.setOpacity(painter.opacity() * text_opacity)
            painter.drawText(rect, Qt.AlignCenter, text)
            labels.append(rect)
    painter.restore()
