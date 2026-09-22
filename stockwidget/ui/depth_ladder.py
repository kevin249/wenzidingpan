"""主题2：纵向千档盘口分布 + 中央现价/涨跌幅。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..config import Config
from ..mcp_depth import DepthSnapshot
from .theme import MUTED, make_font

BID_COLOR = QColor(240, 79, 90)
ASK_COLOR = QColor(34, 197, 94)


class DepthLadder(QWidget):
    """把千档按价格从上到下压到当前卡片高度，中央只留价格与涨跌幅。"""

    price_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._depth: DepthSnapshot | None = None
        self._price: float | None = None
        self._percent: float | None = None
        self._price_color = QColor(220, 224, 234)
        self._error = ""
        self._config = Config()
        self._preferred_width = 168
        self._preferred_height = 104
        self._price_rect = QRectF()
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def apply_config(self, config: Config) -> None:
        self._config = config
        self._preferred_width = max(148, round(config.font_size * 12.5))
        self._preferred_height = max(88, round(config.font_size * 7.2))
        self.setFixedSize(self._preferred_width, self._preferred_height)
        self.updateGeometry()
        self.update()

    def set_depth(self, snapshot: DepthSnapshot | None) -> None:
        if snapshot != self._depth:
            self._depth = snapshot
            self.update()

    def set_quote(
        self,
        price: float | None,
        percent: float | None,
        color: QColor,
        error: str | None = None,
    ) -> None:
        state = (price, percent, color.rgba(), str(error or ""))
        old = (self._price, self._percent, self._price_color.rgba(), self._error)
        if state != old:
            self._price = price
            self._percent = percent
            self._price_color = QColor(color)
            self._error = str(error or "")
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._preferred_width, self._preferred_height)

    def _draw_depth(self, painter: QPainter) -> None:
        snapshot = self._depth
        if snapshot is None or not snapshot.available or not snapshot.levels:
            return

        levels = [row for row in snapshot.levels if row.price > 0 and row.volume > 0]
        if not levels:
            return
        low = min(row.price for row in levels)
        high = max(row.price for row in levels)
        span = high - low or max(abs(high) * 0.001, 0.01)
        height = max(1, self.height() - 4)
        center = self.width() / 2.0
        max_bar = max(12.0, self.width() * 0.47)

        buckets: dict[tuple[int, str], float] = {}
        for row in levels:
            y = round(2 + (high - row.price) / span * height)
            y = max(2, min(self.height() - 2, y))
            key = (y, row.side)
            buckets[key] = buckets.get(key, 0.0) + row.volume
        if not buckets:
            return
        maximum = max(buckets.values()) or 1.0

        for (y, side), volume in buckets.items():
            length = max(1.0, max_bar * volume / maximum)
            if self._config.grayscale:
                color = QColor(145, 145, 145) if side == "bid" else QColor(95, 95, 95)
            else:
                color = QColor(BID_COLOR if side == "bid" else ASK_COLOR)
            color.setAlpha(112)
            painter.setPen(QPen(color, 1.2))
            if side == "bid":
                painter.drawLine(round(center - length), y, round(center), y)
            else:
                painter.drawLine(round(center), y, round(center + length), y)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._draw_depth(painter)

        box_width = min(self.width() * 0.72, max(88.0, self.width() * 0.58))
        box_height = min(54.0, max(38.0, self.height() * 0.42))
        self._price_rect = QRectF(
            (self.width() - box_width) / 2.0,
            (self.height() - box_height) / 2.0,
            box_width,
            box_height,
        )
        backing = QColor(self._config.background_color)
        backing.setAlpha(224)
        painter.setPen(Qt.NoPen)
        painter.setBrush(backing)
        painter.drawRoundedRect(self._price_rect, 7, 7)

        if self._error:
            painter.setPen(QColor(MUTED))
            painter.setFont(make_font(self._config, pixel_size=max(8, self._config.stock_percent_font_size)))
            painter.drawText(self._price_rect, Qt.AlignCenter, self._error[:18])
            return

        price = "--" if self._price is None else f"{self._price:.2f}"
        percent = "--" if self._percent is None else f"{'+' if self._percent > 0 else ''}{self._percent:.2f}%"
        top = QRectF(
            self._price_rect.x(),
            self._price_rect.y() + 2,
            self._price_rect.width(),
            self._price_rect.height() * 0.57,
        )
        bottom = QRectF(
            self._price_rect.x(),
            self._price_rect.y() + self._price_rect.height() * 0.55,
            self._price_rect.width(),
            self._price_rect.height() * 0.38,
        )
        painter.setPen(self._price_color)
        painter.setFont(
            make_font(self._config, bold=True, pixel_size=max(10, self._config.stock_price_font_size))
        )
        painter.drawText(top, Qt.AlignCenter, price)
        painter.setFont(
            make_font(
                self._config,
                bold=self._config.stock_percent_bold,
                pixel_size=max(8, self._config.stock_percent_font_size),
            )
        )
        painter.drawText(bottom, Qt.AlignCenter, percent)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._price_rect.contains(event.position()):
            self.price_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
