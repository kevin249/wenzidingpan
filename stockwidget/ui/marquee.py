"""单行模式：所有股票在一行里横向滚动，鼠标悬停暂停。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..config import Config
from ..providers.base import Quote
from .theme import MUTED, direction_color, fmt_change, fmt_money, fmt_price, make_font

SPEED_PX_PER_SEC = 40
GAP_PX = 28


@dataclass
class Segment:
    text: str
    color: QColor
    bold: bool


class Marquee(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []
        self._offset = 0.0
        self._content_width = 0
        self._paused = False
        self._config: Config | None = None
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # 约 60fps
        self._timer.timeout.connect(self._step)

    # ------------------------------------------------------------ 数据

    def apply_config(self, config: Config) -> None:
        self._config = config
        self.setFont(make_font(config))
        self.setFixedHeight(round(config.font_size * 1.9))
        self._measure()

    def set_quotes(self, quotes: list[Quote]) -> None:
        config = self._config
        if config is None:
            return
        segments: list[Segment] = []
        for quote in quotes:
            color = direction_color(config, quote.change)
            segments.append(Segment(quote.name or quote.symbol, MUTED if quote.error else color, True))
            if quote.error:
                segments.append(Segment(quote.error, MUTED, False))
            else:
                segments.append(Segment(fmt_price(quote.price), color, False))
                segments.append(Segment(fmt_change(quote.change, quote.change_percent), color, False))
                if config.show_dark_trade and (text := fmt_money(quote.dark_fund)):
                    segments.append(Segment(f"暗盘 {text}", MUTED, False))
        self._segments = segments
        self._measure()

    def _measure(self) -> None:
        if self._config is None:
            return
        metrics_normal = QFontMetrics(make_font(self._config))
        metrics_bold = QFontMetrics(make_font(self._config, bold=True))
        width = 0
        for index, segment in enumerate(self._segments):
            metrics = metrics_bold if segment.bold else metrics_normal
            # 每只股票之间留空，段内只留一个空格的间距
            width += metrics.horizontalAdvance(segment.text)
            width += GAP_PX if segment.bold and index else 8
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
                painter.setFont(make_font(self._config, bold=segment.bold))
                if segment.bold and index:
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
