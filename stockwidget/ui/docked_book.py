"""主题 4：透明千档盘口与按成交时间绘制的实时分时线。"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .. import tdw
from ..config import THEME4_MAX_WIDTH, THEME4_MIN_WIDTH, Config
from ..mcp_depth import depth_is_stale, depth_time_label
from ..providers.base import Quote
from ..symbols import classify
from . import tdw_overlay
from .theme import (
    configured_text_color, direction_color, down_color, fmt_money, fmt_price,
    make_font, region_text_color, up_color, with_opacity,
)
from .time_axis import SESSION_MINUTES, average_points, trading_minute


PLOT_MARGIN = 42  # 为股票标题留白，绘图区关于现价对称
PLOT_EDGE = 4
CHART_TO_BOOK_WIDTH = 2
PRICE_AXIS_GUTTER = 7
PRICE_BAND_PERCENT = 2
RESIZE_EDGE = 10
MIN_ROW_HEIGHT = 46
HEADER_PADDING = 6
HEADER_GAP = 6
HEADER_HEIGHT_FRACTION = .23


def reference_prices(price: float) -> tuple[float, float]:
    value = Decimal(str(price))
    precision = Decimal("0.01") if price >= 1 else Decimal("0.0001")
    return tuple(float((value * factor).quantize(precision, rounding=ROUND_HALF_UP))
                 for factor in (Decimal("1.01"), Decimal("0.99")))


def price_ticks(low: float, high: float, count: int) -> tuple[float, ...]:
    midpoint = (low + high) / 2
    if count <= 1 or low == high:
        return (midpoint,)
    precision = .0001 if high < 1 else .01
    target = max(precision, (high - low) / count)
    unit = 10 ** math.floor(math.log10(target))
    step = next(unit * factor for factor in (1, 2, 5, 10) if unit * factor >= target * (1 - 1e-12))
    first = math.ceil(low / step - 1e-9)
    last = math.floor(high / step + 1e-9)
    return tuple(i * step for i in range(first, last + 1)) or (midpoint,)


def volume_gray(fraction: float, dark_desktop: bool) -> int:
    pale, deep = (245, 153) if dark_desktop else (135, 22)
    return round(pale + (deep - pale) * fraction)


class DockedBook(QWidget):
    depth_painted = Signal(object, bool)
    resize_preview = Signal(dict)
    resize_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = Config()
        self.quotes = {}
        self.depths = {}
        self.trends = {}
        self.expanded = set()
        self.first = 0
        self._receipts = {}
        self._pressed = None
        self._resize_axes = (False, False)
        self._resize_start = None
        self.setMouseTracking(True)
        self._stale_timer = QTimer(self, interval=1000)
        self._stale_timer.timeout.connect(self._expire)

    def configure(self, config, depths):
        self.config = config
        self.depths = depths
        self._receipts.clear()
        wanted = set(config.symbols)
        self.quotes = {key: value for key, value in self.quotes.items() if key in wanted}
        self.expanded.intersection_update(wanted)
        self.first = min(self.first, max(0, len(config.symbols) - self.page_size()))
        self.update()

    def set_snapshot(self, snapshot):
        self.quotes = {q.symbol: q for q in snapshot.quotes}
        self.trends = snapshot.trends
        self.update()

    def page_size(self):
        return min(2, max(1, len(self.config.symbols)))

    def percent_spacing(self):
        return (self.config.stock_price_font_size + self.config.chart_label_font_size) / 2 + 6

    def preferred_height(self):
        margin = self._label_margin()
        row = max(2 * (max(PLOT_MARGIN, margin) + PRICE_BAND_PERCENT * self.percent_spacing()),
                  margin / HEADER_HEIGHT_FRACTION)
        return math.ceil(row * self.page_size())

    def minimum_height(self):
        return MIN_ROW_HEIGHT * self.page_size()

    def _label_margin(self):
        config = self.config
        return max(config.stock_name_font_size, config.stock_price_font_size, config.stock_percent_font_size,
                   config.chart_label_font_size, config.dark_trade_font_size) + 9

    def visible_symbols(self):
        return self.config.symbols[self.first:self.first + self.page_size()]

    def showEvent(self, event):
        self._stale_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._stale_timer.stop()
        self._receipts.clear()
        self._pressed = None
        self._resize_start = None
        super().hideEvent(event)

    def _expire(self):
        for snapshot, drawn in self._receipts.values():
            if drawn and depth_is_stale(snapshot):
                self.update()
                break

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            last = max(0, len(self.config.symbols) - self.page_size())
            self.first = min(last, max(0, self.first + (-1 if delta > 0 else 1)))
            self._receipts.clear()
            self.update()
        event.accept()

    def resize_edges(self, point):
        inner = point.x() if self._resize_from_left() else self.width() - point.x()
        return inner <= RESIZE_EDGE, self.height() - point.y() <= RESIZE_EDGE

    def _resize_from_left(self):
        return self.config.theme4_snap and self.config.theme4_side == "right"

    def _resize_patch(self, point):
        offset = point - self._pressed
        start = self._resize_start
        width, height = self._resize_axes
        patch = {}
        if width and offset.x():
            dx = -offset.x() if self._resize_from_left() else offset.x()
            patch["theme4_width"] = min(THEME4_MAX_WIDTH, max(THEME4_MIN_WIDTH, start.width() + dx))
        if height and offset.y():
            patch["theme4_height"] = max(1, start.height() + offset.y())
        return patch

    def mouseMoveEvent(self, event):
        if self._resize_start is not None:
            self.resize_preview.emit(self._resize_patch(event.globalPosition().toPoint()))
            width, height = self._resize_axes
        else:
            width, height = self.resize_edges(event.position())
        if width and height:
            cursor = Qt.SizeBDiagCursor if self._resize_from_left() else Qt.SizeFDiagCursor
        else:
            cursor = Qt.SizeHorCursor if width else Qt.SizeVerCursor if height else Qt.PointingHandCursor
        self.setCursor(cursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = event.globalPosition().toPoint()
            self._resize_axes = self.resize_edges(event.position())
            if any(self._resize_axes):
                self._resize_start = self.window().size()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._pressed is None:
            return
        point = event.globalPosition().toPoint()
        offset = point - self._pressed
        patch = self._resize_patch(point) if self._resize_start is not None else None
        self._resize_start = None
        self._pressed = None
        if patch is not None:
            if patch:
                self.resize_requested.emit(patch)
            else:
                self.resize_preview.emit({})
        elif (offset.manhattanLength() < QApplication.startDragDistance()
              and self.rect().contains(event.position().toPoint())):
            index = int(event.position().y() * self.page_size() / max(1, self.height()))
            symbols = self.visible_symbols()
            if index < len(symbols) and self.config.stock_component_visible(symbols[index], "chart"):
                self.expanded.symmetric_difference_update({symbols[index]})
                self.update()
        event.accept()

    def cancel_click(self):
        self._pressed = None

    def _font(self, size, bold=False, italic=False):
        font = make_font(self.config, pixel_size=size, bold=bold)
        font.setItalic(italic)
        return font

    def _quote_ink(self, setting, change, ink, region):
        config = self.config
        if not config.grayscale:
            automatic = direction_color(config, change) if change else ink
            ink = configured_text_color(setting, automatic)
        return region_text_color(config, ink, region)

    @staticmethod
    def _fit_labels(p, labels, limit, measure):
        fonts = [QFont(font) for _, font in labels]
        base_sizes = [font.pixelSize() for font in fonts]
        scale = 1.0
        step = 1 / max(base_sizes)
        while True:
            widths = [QFontMetrics(font, p.device()).horizontalAdvance(text)
                      for (text, _), font in zip(labels, fonts)]
            extent = measure(widths)
            if extent <= limit or all(font.pixelSize() <= 7 for font in fonts):
                return fonts, widths
            scale = min(scale - step, scale * limit / extent)
            for font, size in zip(fonts, base_sizes):
                font.setPixelSize(max(7, round(size * scale)))

    def _draw_text(self, p, text, rect, align, ink, *, shadow=False):
        if ink.alpha() == 0:
            return
        metrics = p.fontMetrics()
        limit = max(1, int(rect.width()))
        if metrics.horizontalAdvance(text) > limit:
            text = metrics.elidedText(text, Qt.ElideRight, limit)
        width = metrics.horizontalAdvance(text)
        x = rect.left()
        if align == Qt.AlignRight:
            x = rect.right() - width
        elif align == Qt.AlignHCenter:
            x = rect.center().x() - width / 2
        baseline = QPointF(x, rect.center().y() + (metrics.ascent() - metrics.descent()) / 2)
        if shadow:
            glyphs = QPainterPath()
            glyphs.addText(baseline + QPointF(1.7, 1.7), p.font(), text)
            p.fillPath(glyphs, with_opacity(QColor("#000000"), .7 * ink.alphaF()))
        p.setPen(ink)
        p.drawText(baseline, text)

    def _header_fields(self, symbol, quote, code, depth, ink, margin, price_font, price_ink):
        config = self.config
        max_size = max(7, int(margin - 4))
        fields = []
        if config.stock_component_visible(symbol, "name") and quote.name and quote.name not in (symbol, code):
            fields.append((quote.name, self._font(min(config.stock_name_font_size, max_size), config.stock_name_bold,
                                                 config.theme4_name_italic),
                           self._quote_ink(config.stock_name_color, quote.change_percent, ink, "name"),
                           config.theme4_name_shadow))
        stamp = depth_time_label(depth).partition(" ")[2] if depth and depth.available and depth.levels else ""
        fields.append((stamp or "—", price_font, price_ink, config.theme4_price_shadow))
        if config.stock_component_visible(symbol, "dark"):
            fields.append((fmt_money(quote.dark_fund) or "—",
                           self._font(min(config.dark_trade_font_size, max_size), config.dark_trade_bold,
                                      config.theme4_dark_italic),
                           self._quote_ink(config.dark_trade_color, quote.dark_fund, ink, "dark"),
                           config.theme4_dark_shadow))
        change = quote.change_percent
        if config.stock_component_visible(symbol, "percent") and change is not None and math.isfinite(change):
            sign = "+" if change > 0 else ""
            fields.append((f"{sign}{change:.2f}%",
                           self._font(min(config.stock_percent_font_size, max_size), config.stock_percent_bold),
                           self._quote_ink(config.stock_percent_color, change, ink, "percent"), False))
        return fields

    def _draw_header(self, p, fields, fonts, widths, left, margin):
        gaps = (len(fields) - 1) * HEADER_GAP
        available = max(1, self.width() - 2 * HEADER_PADDING)
        extent = sum(widths) + gaps
        stretch = min(1, available / max(1, extent))
        p.save()
        p.translate(HEADER_PADDING if left else self.width() - HEADER_PADDING - extent * stretch, 0)
        p.scale(stretch, 1)
        x = 0
        for (text, _, color, shadow), font, width in zip(fields, fonts, widths):
            p.setFont(font)
            self._draw_text(p, text, QRectF(x, 2, width, margin - 4), Qt.AlignLeft, color, shadow=shadow)
            x += width + HEADER_GAP
        p.restore()
        return stretch

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        row_height = self.height() / self.page_size()
        for i, symbol in enumerate(self.visible_symbols()):
            painter.save()
            painter.translate(0, i * row_height)
            painter.setClipRect(QRectF(0, 0, self.width(), row_height))
            self._paint_stock(painter, symbol, row_height)
            painter.restore()
        painter.end()

    def _paint_stock(self, p, symbol, height):
        width = self.width()
        config = self.config
        left = config.theme4_side == "left"
        dark = config.theme4_tone == "dark"
        ink = QColor("#fafafa" if dark else "#171717")
        rule = QColor("#777777")
        label_ink = region_text_color(config, ink, "chart")
        margin = min(max(PLOT_MARGIN, self._label_margin()), height * HEADER_HEIGHT_FRACTION)
        quote = self.quotes.get(symbol) or Quote(symbol, symbol)
        price_ink = self._quote_ink(config.stock_price_color, quote.change_percent, ink, "price")
        parsed = classify(symbol)
        code = parsed.code if parsed else symbol
        depth = self.depths.get(code)
        stale = depth is not None and depth_is_stale(depth)
        levels = depth.levels if depth and depth.available and not stale else ()

        price = quote.price
        have_price = bool(price and math.isfinite(price) and price > 0 and not quote.error)
        visible_chart = symbol in self.expanded and config.stock_component_visible(symbol, "chart")
        center = height / 2
        spacing = (center - margin) / PRICE_BAND_PERCENT
        max_size = max(7, math.floor(spacing))
        price_font = self._font(min(config.stock_price_font_size, max_size, max(7, int(margin - 4))),
                                config.stock_price_bold, config.theme4_price_italic)
        header = self._header_fields(symbol, quote, code, depth, ink, margin, price_font, price_ink)
        count = len(header)
        labels = []
        content_width = width - 2 * PLOT_EDGE
        chart_padding = max(3, max(config.chart_line_width, config.average_line_width) / 2 + 1)
        axis_width = 0
        if have_price:
            upper, lower = reference_prices(price)
            reference_font = self._font(min(config.chart_label_font_size, max_size),
                                        config.stock_price_bold, config.theme4_price_italic)
            labels = [(fmt_price(upper), reference_font), (fmt_price(price), price_font),
                      (fmt_price(lower), reference_font)]
            # 收起时同样预留坐标轴，点击图表不会重新分配盘口宽度。
            axis_width = self._price_axis_width(p, labels, content_width)
        plot_budget = content_width - axis_width - 2 * chart_padding
        minimum_book_width = max(8, width * .13)
        lane_units = 1 + CHART_TO_BOOK_WIDTH
        price_limit = plot_budget - lane_units * minimum_book_width
        header_limit = max(1, width - 2 * HEADER_PADDING - (count - 1) * HEADER_GAP)

        def fit_extent(widths):
            return max(sum(widths[:count]) / header_limit,
                       max(widths[count:], default=0) / max(1, price_limit - 6))

        fonts, widths = self._fit_labels(p, [(text, font) for text, font, _, _ in header] + labels, 1, fit_extent)
        stretch = self._draw_header(p, header, fonts[:count], widths[:count], left, margin)
        if have_price:
            value = Decimal(str(price))
            radius = value * PRICE_BAND_PERCENT / 100
            low, high = float(value - radius), float(value + radius)
            levels = [level for level in levels if low <= level.price <= high and level.volume > 0]
            scale = max(1, center - margin) / float(radius)

            def y(value):
                return center - (value - price) * scale

            line_ys = (center - spacing, center, center + spacing)
            label_width = min(max(widths[count:]), math.floor((price_limit - 6) / stretch))
            price_width = label_width * stretch + 6
            book_width = (plot_budget - price_width) / lane_units
            book_edge = PLOT_EDGE if left else width - PLOT_EDGE
            book_end = book_edge + (book_width if left else -book_width)
            price_start = book_end if left else book_end - price_width
            if levels:
                self._draw_book(p, levels, y, book_edge, book_end - book_edge, dark)
            if visible_chart:
                chart_start = price_start + price_width if left else PLOT_EDGE
                chart_rect = QRectF(chart_start, margin, content_width - book_width - price_width,
                                    height - 2 * margin)
                trend = self.trends.get(symbol)
                if trend is not None and trend.realtime_bars and len(trend.realtime_bars[-1].time) > 16:
                    self._draw_chart(p, trend, chart_rect, chart_padding, axis_width, ink)
                else:
                    p.setFont(self._font(min(config.chart_label_font_size, max_size)))
                    plot_rect = chart_rect.adjusted(chart_padding, 0, -chart_padding - axis_width, 0)
                    self._draw_text(p, "等待实时分时", plot_rect, Qt.AlignHCenter, label_ink)
            p.setPen(QPen(rule, .8, Qt.DashLine, Qt.FlatCap))
            for line_y in line_ys:
                p.drawLine(QPointF(book_edge, line_y), QPointF(book_end, line_y))
            if config.stock_component_visible(symbol, "price"):
                self._draw_prices(p, labels, fonts[count:], line_ys, price_start + 3, label_width,
                                  stretch, price_ink, left)
        else:
            p.setFont(self._font(config.chart_label_font_size))
            self._draw_text(p, quote.error or "等待现价", QRectF(4, height / 2 - 20, width - 8, 40),
                            Qt.AlignHCenter, label_ink)
        if depth is not None:
            drawn = bool(levels and have_price)
            last = self._receipts.get(symbol)
            if last is None or last[0] is not depth or last[1] != drawn:
                self._receipts[symbol] = (depth, drawn)
                self.depth_painted.emit(depth, drawn)

    def _price_axis_width(self, p, labels, content_width):
        font = self._font(min(self.config.chart_label_font_size, 12))
        metrics = QFontMetrics(font, p.device())
        width = max(metrics.horizontalAdvance(label) for label, _ in labels) + PRICE_AXIS_GUTTER
        limit = math.floor(content_width / 4)
        if width <= limit:
            return width
        font.setPixelSize(7)
        metrics = QFontMetrics(font, p.device())
        width = max(metrics.horizontalAdvance(label) for label, _ in labels) + PRICE_AXIS_GUTTER
        return limit if width <= limit else 0

    def _draw_prices(self, p, labels, fonts, line_ys, start, width, stretch, ink, left):
        align = Qt.AlignLeft if left else Qt.AlignRight
        p.save()
        p.translate(start, 0)
        p.scale(stretch, 1)
        for (label, _), font, cy in zip(labels, fonts, line_ys):
            size = font.pixelSize()
            p.setFont(font)
            rect = QRectF(0, cy - size, width, size * 2)
            self._draw_text(p, label, rect, align, ink, shadow=self.config.theme4_price_shadow)
        p.restore()

    def _draw_book(self, p, levels, y, edge, extent, dark):
        peak = max(level.volume for level in levels)
        if peak <= 0:
            return
        hues = {} if self.config.grayscale else {
            "bid": up_color(self.config).hslHueF(), "ask": down_color(self.config).hslHueF(),
        }
        shades = {}
        for level in levels:
            fraction = level.volume / peak
            gray = volume_gray(fraction, dark)
            line_y = y(level.price)
            shade = gray, hues.get(level.side, -1)
            shades.setdefault(shade, []).append(QLineF(edge, line_y, edge + extent * fraction, line_y))
        line_width = self.config.depth_line_width
        for gray, hue in sorted(shades, reverse=True):
            if hue < 0:
                color = QColor(gray, gray, gray)
            else:
                color = QColor.fromHslF(hue, .82, .2 + .6 * gray / 255)
            p.setPen(QPen(color, line_width, Qt.SolidLine, Qt.FlatCap))
            p.drawLines(shades[gray, hue])

    def _draw_chart(self, p, trend, rect, padding, axis_width, ink):
        bars = trend.realtime_bars
        config = self.config
        chart_width, chart_height = rect.width(), rect.height()
        tdw_height = round(chart_height / 3)
        price_height = chart_height - tdw_height
        p.save()
        p.translate(rect.left() + padding, rect.top())
        p.setClipRect(QRectF(-padding, 0, chart_width, chart_height), Qt.IntersectClip)
        plot_width = chart_width - axis_width - 2 * padding
        line_width = config.chart_line_width
        fractions = [(trading_minute(bar.time[11:]) or 0) / SESSION_MINUTES for bar in bars]
        averages = average_points(trend.average_prices)
        closes = [bar.close for bar in bars]
        plotted_prices = closes + [price for _, price in averages]
        low, high = min(plotted_prices), max(plotted_prices)
        span = high - low
        half_stroke = max(line_width, config.average_line_width) / 2
        plot_bottom = price_height - half_stroke
        scale = (plot_bottom - price_height * .09) / span if span else 0

        def session_x(fraction):
            return plot_width * fraction

        def x(i):
            return session_x(fractions[i])

        def y(price):
            return plot_bottom - (price - low) * scale if span else price_height / 2

        def chart_ink(color, alpha=None):
            color = QColor(ink if config.grayscale else color)
            if alpha is not None:
                color.setAlpha(alpha)
            return color

        color = chart_ink(config.chart_line_color)
        pen = QPen(color, line_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        path = tdw_overlay.series_path(closes, x, y, spacing=1)
        if path.elementCount() > 1:
            p.strokePath(path, pen)
        else:
            p.setPen(pen)
            p.drawPoint(QPointF(x(len(closes) - 1), y(closes[-1])))
        if averages:
            average_color = QColor("#808080" if config.grayscale else config.average_line_color)
            average = tdw_overlay.series_path((price for _, price in averages),
                                               lambda i: session_x(averages[i][0]), y)
            p.strokePath(average, QPen(average_color, config.average_line_width))

        power, events = tdw.align(trend.tdw_points, (bar.time for bar in bars))
        p.save()
        p.translate(0, price_height)
        guides = tdw_overlay.draw_power_panel(p, power, x, session_x(1), plot_width, tdw_height,
                                               chart_ink, ink, show_caption=False)
        p.restore()
        if config.show_bs_points and guides:
            markers = [(i, tag) for i, _, tag in tdw_overlay.connector_points(events, power, len(bars))]
            for i, tag in tdw_overlay.visible_events(markers, len(bars), x):
                tdw_overlay.draw_tdw_exit(p, x(i), y(bars[i].close), price_height + guides[tag], chart_ink)
        if axis_width:
            axis_font = self._font(min(config.chart_label_font_size, 12))
            tick_count = min(5, int(price_height / (axis_font.pixelSize() + 7)))
            ticks = price_ticks(low, high, tick_count)
            labels = [fmt_price(value) for value in ticks]
            label_width = axis_width - PRICE_AXIS_GUTTER
            fonts, _ = self._fit_labels(p, [(label, axis_font) for label in labels], label_width, max)
            p.setFont(fonts[0])
            axis_ink = region_text_color(config, ink, "chart")
            tick_pen = QPen(with_opacity(axis_ink, .45), .8)
            axis_x = plot_width + padding
            p.setPen(tick_pen)
            p.drawLine(QPointF(axis_x, 0), QPointF(axis_x, price_height))
            for value, label in zip(ticks, labels):
                cy = y(value)
                p.setPen(tick_pen)
                p.drawLine(QPointF(axis_x, cy), QPointF(axis_x + 3, cy))
                self._draw_text(p, label, QRectF(axis_x + 5, cy - 5, label_width, 10), Qt.AlignRight, axis_ink)
        p.restore()
