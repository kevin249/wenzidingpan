"""低干扰悬浮文字：横向一行 / 贴边小列，共用按需展开的分时浮层。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..config import Config
from . import tdw_overlay
from .theme import direction_color, display_color, fmt_money, fmt_price, make_font, text_color
from .time_axis import average_points, intraday_fraction

STEALTH_THEMES = ("depth", "radar")


def draw_trend(painter: QPainter, rect: QRectF, values: list[float], color: QColor, *,
               line_width=1.0, minutes=(), averages=(), average_color=QColor("#ffff00"), average_width=1.5,
               tdw_events=(), tdw_power=(), paint_color=QColor) -> None:
    """不画坐标网格，只保留真实走势；无数据时不伪造折线。"""
    if len(values) < 2 or rect.width() <= 0 or rect.height() <= 0:
        return
    connectors = list(tdw_overlay.connector_points(tdw_events, tdw_power, len(values)))
    lane_height = 16 if connectors and rect.height() >= 40 else 0
    price_rect = rect.adjusted(0, 0, 0, -lane_height)
    average = average_points(averages, minutes, fill_width=True)
    prices = [*values, *(price for _, price in average)]
    low, high = min(prices), max(prices)
    spread = high - low
    inset = 2 if lane_height else 0

    def y_of(value):
        if spread == 0:
            return price_rect.center().y()
        return price_rect.bottom() - inset - (value - low) / spread * (price_rect.height() - inset)

    def x_of(index):
        return intraday_fraction(index, len(values), minutes, fill_width=True) * rect.width()

    painter.save()
    painter.setClipRect(rect.adjusted(-3, -3, 3, 3), Qt.IntersectClip)
    average_path = QPainterPath()
    for fraction, price in average:
        point = QPointF(rect.left() + fraction * rect.width(), y_of(price))
        if average_path.elementCount():
            average_path.lineTo(point)
        else:
            average_path.moveTo(point)
    painter.setPen(QPen(average_color, average_width))
    painter.drawPath(average_path)
    path = QPainterPath()
    for index, value in enumerate(values):
        x = rect.left() + x_of(index)
        y = y_of(value)
        point = QPointF(x, y)
        if index:
            path.lineTo(point)
        else:
            path.moveTo(point)
    painter.setPen(QPen(color, line_width))
    painter.drawPath(path)
    painter.translate(rect.topLeft())
    if lane_height:
        painter.save()
        painter.translate(0, price_rect.height())
        tdw_overlay.draw_power(painter, tdw_power, x_of, lane_height, paint_color(tdw_overlay.POWER_COLOR))
        painter.restore()
        for index, value, _ in connectors:
            tdw_y = price_rect.height() + tdw_overlay.power_y(value, lane_height)
            tdw_overlay.draw_tdw_exit(painter, x_of(index), y_of(values[index]) - rect.top(), tdw_y, paint_color)
    painter.restore()


class StealthPeek(QWidget):
    """非激活工具窗，不抢工作窗口焦点；主窗口隐藏时同步关闭。"""

    def __init__(self, panel: "StealthPanel") -> None:
        super().__init__(panel.window(), Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.panel = panel
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAccessibleName("股票临时详情")

    def enterEvent(self, event) -> None:  # noqa: N802
        self.panel.dismiss_timer.stop()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.panel.dismiss_timer.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        panel = self.panel
        quote = panel.selected_quote()
        if quote is None:
            return
        config = panel.config
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        background = QColor(config.background_color)
        painter.setBrush(background)
        painter.setPen(QColor(110, 116, 125, 75))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)
        foreground = panel.color(quote, "name")
        painter.setPen(foreground)
        painter.setFont(make_font(config, pixel_size=config.font_size))
        heading = []
        if config.stock_component_visible(quote.symbol, "name"):
            heading.append(quote.name or quote.symbol)
        if config.stock_component_visible(quote.symbol, "price"):
            heading.append(fmt_price(quote.price))
        metrics = painter.fontMetrics()
        painter.drawText(QRect(12, 9, self.width() - 24, metrics.height() + 4),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText("  ".join(heading), Qt.ElideRight, self.width() - 24))
        values, minutes = panel.series(quote.symbol)
        chart = QRectF(12, metrics.height() + 20, self.width() - 24, 48)
        painter.setPen(panel.color(quote, "chart"))
        if config.stock_component_visible(quote.symbol, "chart"):
            panel.draw_chart(painter, chart, quote.symbol, show_markers=True)
            if len(values) < 2:
                painter.drawText(chart, Qt.AlignCenter, "等待分时数据")
        painter.setFont(make_font(config, pixel_size=max(7, config.font_size - 1)))
        foot = []
        if config.stock_component_visible(quote.symbol, "dark"):
            foot.append(f"暗 {fmt_money(quote.dark_fund) or '—'}")
        if quote.error:
            foot = [f"行情异常 · {quote.error}"]
        elif quote.halted:
            foot.append("停牌")
        elif minutes:
            foot.append(minutes[-1])
        painter.setPen(panel.color(quote, "dark"))
        bottom = QRect(12, self.height() - painter.fontMetrics().height() - 10,
                       self.width() - 24, painter.fontMetrics().height())
        painter.drawText(bottom, Qt.AlignLeft | Qt.AlignVCenter,
                         painter.fontMetrics().elidedText("   ".join(foot), Qt.ElideRight, bottom.width()))


class StealthPanel(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.config = Config()
        self.quotes = []
        self.rows = {}
        self.selected_symbol: str | None = None
        self.cells: list[tuple[QRect, object]] = []
        self._scroll_offset = 0
        self.setMouseTracking(True)
        self.setAccessibleName("隐蔽行情悬浮条")
        self.peek = StealthPeek(self)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.setInterval(180)
        self.dismiss_timer.timeout.connect(self._dismiss_if_outside)

    def apply_config(self, config: Config) -> None:
        self.dismiss()
        if config.display_theme != self.config.display_theme:
            self._scroll_offset = 0
        self.config = config
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
        if config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.peek.setWindowFlags(flags)
        self.peek.setWindowOpacity(config.opacity)
        self.setFont(make_font(config, pixel_size=config.font_size))
        self.updateGeometry()
        self.update()

    def set_quotes(self, quotes, rows) -> None:
        self.quotes = list(quotes)
        self.rows = rows
        if self.selected_quote() is None:
            self.dismiss()
        self.updateGeometry()
        self.update()
        if self.peek.isVisible():
            self.peek.update()

    def selected_quote(self):
        return next((q for q in self.quotes if q.symbol == self.selected_symbol), None)

    def series(self, symbol):
        row = self.rows.get(symbol)
        if row is None:
            return [], []
        chart = row.sparkline
        values = list(chart._series or chart._samples)
        minutes = list(chart._minutes or chart._sample_minutes)
        return values, minutes

    def color(self, quote, region: str) -> QColor:
        config = self.config
        field = {"name": "stock_name_color", "price": "stock_price_color",
                 "percent": "stock_percent_color", "dark": "dark_trade_color"}.get(region)
        normal = direction_color(config, quote.change)
        return text_color(config, getattr(config, field) if field else "auto", normal, region=region)

    def draw_chart(self, painter, rect, symbol, *, show_markers=False):
        values, minutes = self.series(symbol)
        row = self.rows.get(symbol)
        config = self.config
        show_markers = show_markers and row is not None and row.sparkline._show_signals
        def ink(value):
            return display_color(QColor(value), grayscale=config.grayscale, level=config.grayscale_level)
        draw_trend(painter, rect, values, ink(config.chart_line_color),
                   line_width=config.chart_line_width, minutes=minutes,
                   averages=row.sparkline._averages if row else (),
                   average_color=ink(config.average_line_color), average_width=config.average_line_width,
                   tdw_events=row.sparkline._tdw_events if show_markers else (),
                   tdw_power=row._minute_tdw_power if show_markers else (), paint_color=ink)

    def _parts(self, quote):
        config = self.config
        metrics = QFontMetrics(self.font())
        parts = []
        if config.stock_component_visible(quote.symbol, "name"):
            name = (quote.name or quote.symbol)[:2] if quote.name and quote.name != quote.symbol else quote.symbol
            parts.append(("name", name, metrics.horizontalAdvance(name)))
        if config.display_theme == "radar" and config.stock_component_visible(quote.symbol, "chart"):
            parts.append(("chart", "", max(24, round(config.font_size * 2.8))))
        if config.stock_component_visible(quote.symbol, "price"):
            price = fmt_price(quote.price)
            parts.append(("price", price, metrics.horizontalAdvance("0" * max(2, len(price) - 3) + ".00")))
        if config.stock_component_visible(quote.symbol, "percent"):
            value = f"{quote.change_percent:+.2f}%" if quote.change_percent is not None else "—"
            if quote.error:
                value = "异常"
            elif quote.halted:
                value = "停牌"
            parts.append(("percent", value, max(metrics.horizontalAdvance("+00.00%"), metrics.horizontalAdvance(value))))
        return parts

    def _cell_size(self, quote) -> QSize:
        parts = self._parts(quote)
        gap = max(5, round(self.config.font_size * .6))
        width = sum(p[2] for p in parts) + gap * max(0, len(parts) - 1) + 12
        height = QFontMetrics(self.font()).height() + (12 if self.config.display_theme == "radar" else 10)
        return QSize(max(24, width), height)

    def sizeHint(self) -> QSize:  # noqa: N802
        sizes = [self._cell_size(q) for q in self.quotes]
        if not sizes:
            return QSize(190, QFontMetrics(self.font()).height() + 10)
        if self.config.display_theme == "radar":
            return QSize(max(s.width() for s in sizes), sum(s.height() for s in sizes))
        return QSize(sum(s.width() for s in sizes), max(s.height() for s in sizes))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self.font())
        # Windows 分层窗口的全透明像素不能命中鼠标；保留肉眼不可见的热区。
        if not self.config.click_through:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        self.cells = []
        if not self.quotes:
            painter.setPen(QColor(self.config.grayscale_level, self.config.grayscale_level, self.config.grayscale_level))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无自选 · 右键设置")
            return
        self._scroll_offset = min(self._scroll_offset, self._max_scroll())
        offset = -self._scroll_offset
        for quote in self.quotes:
            size = self._cell_size(quote)
            vertical = self.config.display_theme == "radar"
            rect = QRect(0, offset, self.width(), size.height()) if vertical else QRect(offset, 0, size.width(), self.height())
            self.cells.append((rect, quote))
            parts = self._parts(quote)
            x = rect.left() + 6
            for region, value, width in parts:
                area = QRectF(x, rect.top(), width, rect.height())
                color = self.color(quote, region)
                painter.setPen(color)
                if region == "chart":
                    self.draw_chart(painter, area.adjusted(0, rect.height() / 2 - 6, 0, -rect.height() / 2 + 6),
                                    quote.symbol)
                else:
                    painter.drawText(area, Qt.AlignVCenter | Qt.AlignLeft, value)
                x += width + max(5, round(self.config.font_size * .6))
            if not vertical and offset:
                divider = QColor(self.config.grayscale_level, self.config.grayscale_level, self.config.grayscale_level, 50)
                painter.setPen(divider)
                painter.drawLine(rect.left(), 7, rect.left(), self.height() - 7)
            offset += size.height() if vertical else size.width()

    def _max_scroll(self) -> int:
        natural = self.sizeHint()
        return max(0, natural.height() - self.height() if self.config.display_theme == "radar"
                   else natural.width() - self.width())

    def wheelEvent(self, event) -> None:  # noqa: N802
        """自选超出屏幕时滚动查看，不添加常驻滚动条。"""
        delta = event.angleDelta().y() or event.angleDelta().x()
        self._scroll_offset = max(0, min(self._max_scroll(), self._scroll_offset - round(delta / 120 * 48)))
        self.dismiss()
        self.update()
        event.accept()

    def show_peek(self, symbol: str) -> None:
        if self.config.click_through or not self.isVisible() or not self.window().isVisible():
            return
        self.selected_symbol = symbol
        if self.selected_quote() is None:
            return
        self.dismiss_timer.stop()
        self.peek.resize(max(250, round(self.config.font_size * 21)), self.fontMetrics().height() * 2 + 86)
        position = self.mapToGlobal(QPoint(0, self.height()))
        if self.config.display_theme == "radar":
            position.setX(self.mapToGlobal(QPoint(self.width(), 0)).x() - self.peek.width())
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            if position.y() + self.peek.height() > available.bottom() + 1:
                position.setY(self.mapToGlobal(QPoint()).y() - self.peek.height())
            position.setX(max(available.left(), min(position.x(), available.right() - self.peek.width() + 1)))
            position.setY(max(available.top(), min(position.y(), available.bottom() - self.peek.height() + 1)))
        self.peek.move(position)
        self.peek.show()
        self.peek.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons():
            self.dismiss()
            return
        for rect, quote in self.cells:
            if rect.contains(event.position().toPoint()):
                self.show_peek(quote.symbol)
                break

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            for rect, quote in self.cells:
                if rect.contains(event.position().toPoint()):
                    self.show_peek(quote.symbol)
                    break

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.dismiss_timer.start()

    def _dismiss_if_outside(self) -> None:
        point = QCursor.pos()
        if not self.rect().contains(self.mapFromGlobal(point)) and not (
            self.peek.isVisible() and self.peek.geometry().contains(point)
        ):
            self.dismiss()

    def dismiss(self) -> None:
        self.dismiss_timer.stop()
        self.peek.hide()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.dismiss()
