"""单行模式：所有股票在一行里横向滚动，鼠标悬停暂停。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..config import Config
from ..providers.base import Quote
from .theme import MUTED, configured_text_color, direction_color, fmt_money, fmt_price, make_font

SPEED_PX_PER_SEC = 40
GAP_PX = 28


@dataclass
class Segment:
    text: str
    color: QColor
    font_size: int
    bold: bool
    starts_quote: bool = False


class Marquee(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self._offset = 0.0
        self._content_width = 0
        self._paused = False
        self._config: Config | None = None
        self._quotes: list[Quote] = []
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # 约 60fps
        self._timer.timeout.connect(self._step)

    # ------------------------------------------------------------ 数据

    def apply_config(self, config: Config) -> None:
        self._config = config
        self.setFont(make_font(config))
        sizes = [config.font_size]
        if config.show_stock_name:
            sizes.append(config.stock_name_font_size)
        if config.show_stock_price:
            sizes.extend((config.stock_price_font_size, config.stock_percent_font_size))
        if config.show_dark_trade:
            sizes.append(config.dark_trade_font_size)
        self.setFixedHeight(round(max(sizes) * 1.9))
        self.set_quotes(self._quotes)

    def set_quotes(self, quotes: list[Quote]) -> None:
        self._quotes = list(quotes)
        config = self._config
        if config is None:
            return
        segments: list[Segment] = []
        for quote in quotes:
            first_segment = len(segments)
            color = direction_color(config, quote.change)
            if config.show_stock_name:
                segments.append(
                    Segment(
                        quote.name or quote.symbol,
                        configured_text_color(config.stock_name_color, color),
                        config.stock_name_font_size,
                        config.stock_name_bold,
                    )
                )
            if quote.error:
                segments.append(
                    Segment(quote.error, MUTED, config.stock_price_font_size, False)
                )
            else:
                if config.show_stock_price:
                    segments.append(
                        Segment(
                            fmt_price(quote.price),
                            configured_text_color(config.stock_price_color, color),
                            config.stock_price_font_size,
                            config.stock_price_bold,
                        )
                    )
                    percent_sign = "+" if quote.change_percent and quote.change_percent > 0 else ""
                    percent = (
                        f"{percent_sign}{quote.change_percent:.2f}%"
                        if quote.change_percent is not None
                        else ""
                    )
                    segments.append(
                        Segment(
                            percent,
                            configured_text_color(config.stock_percent_color, color),
                            config.stock_percent_font_size,
                            config.stock_percent_bold,
                        )
                    )
                if config.show_dark_trade and (text := fmt_money(quote.dark_fund)):
                    dark_color = configured_text_color(
                        config.dark_trade_color,
                        direction_color(config, quote.dark_fund),
                    )
                    segments.append(
                        Segment(
                            f"暗 {text}",
                            dark_color,
                            config.dark_trade_font_size,
                            config.dark_trade_bold,
                        )
                    )
            if len(segments) > first_segment:
                segments[first_segment].starts_quote = True
        self._segments = segments
        self._measure()

    def _measure(self) -> None:
        if self._config is None:
            return
        width = 0
        for index, segment in enumerate(self._segments):
            metrics = QFontMetrics(
                make_font(
                    self._config,
                    bold=segment.bold,
                    pixel_size=segment.font_size,
                )
            )
            # 每只股票之间留空，段内只留一个空格的间距
            width += metrics.horizontalAdvance(segment.text)
            width += GAP_PX if segment.starts_quote and index else 8
        self._content_width = width
        # 内容比窗口窄就没必要滚动
        if self._content_width <= self.width():
            self._timer.stop()
            self._offset = 0.0
        elif not self._timer.isActive():
            self._timer.start()
        self.update()

    # ------------------------------------------------------------ 动画

    def _step(self) -> None:
        if self._paused or not self._content_width:
            return
        self._offset = (self._offset + SPEED_PX_PER_SEC * self._timer.interval() / 1000) % self._content_width
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._paused = True

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._paused = False

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._measure()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if not self._segments or self._config is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        baseline = self.height() // 2

        # 画两遍，第二遍紧跟第一遍尾部，滚动时首尾相接。
        start = -self._offset
        for _ in range(2):
            x = start
            for index, segment in enumerate(self._segments):
                painter.setFont(
                    make_font(
                        self._config,
                        bold=segment.bold,
                        pixel_size=segment.font_size,
                    )
                )
                if segment.starts_quote and index:
                    x += GAP_PX
                elif index:
                    x += 8
                painter.setPen(segment.color)
                metrics = QFontMetrics(painter.font())
                painter.drawText(round(x), baseline + metrics.height() // 3, segment.text)
                x += metrics.horizontalAdvance(segment.text)
            start += self._content_width
            if self._content_width <= self.width():
                break  # 不滚动时不需要第二份
