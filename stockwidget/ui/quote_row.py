"""多行模式下的一行行情。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from ..config import Config
from ..providers.base import Quote
from .sparkline import Sparkline
from .theme import (
    MUTED,
    direction_color,
    fmt_change,
    fmt_money,
    fmt_price,
    make_font,
)


def _color_style(color) -> str:
    return f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"


class QuoteRow(QWidget):
    """一行包含：名称/代码、现价、迷你走势图、涨跌、暗盘资金。"""

    def __init__(self, symbol: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.symbol = symbol

        self.name_label = QLabel()
        self.code_label = QLabel()
        self.price_label = QLabel()
        self.change_label = QLabel()
        self.dark_label = QLabel("暗盘资金")
        self.dark_value = QLabel()
        self.sparkline = Sparkline()

        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.change_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dark_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.code_label.setStyleSheet(_color_style(MUTED))
        self.dark_label.setStyleSheet(_color_style(MUTED))

        header = QWidget()
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        header_layout.addWidget(self.name_label, 0, 0)
        header_layout.addWidget(self.code_label, 0, 1)
        header_layout.setColumnStretch(2, 1)

        layout = QGridLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(1)
        layout.addWidget(header, 0, 0)
        layout.addWidget(self.price_label, 0, 1)
        layout.addWidget(self.sparkline, 1, 0)
        layout.addWidget(self.change_label, 1, 1)
        layout.addWidget(self.dark_label, 2, 0)
        layout.addWidget(self.dark_value, 2, 1)
        layout.setColumnStretch(0, 1)
        self._layout = layout

    # ------------------------------------------------------------ 更新

    def apply_config(self, config: Config) -> None:
        self.name_label.setFont(make_font(config, 0.95, bold=True))
        self.code_label.setFont(make_font(config, 0.8))
        self.price_label.setFont(make_font(config, 1.1, bold=True))
        self.change_label.setFont(make_font(config, 0.85))
        self.dark_label.setFont(make_font(config, 0.8))
        self.dark_value.setFont(make_font(config, 0.8))
        self.sparkline.setFixedHeight(round(config.font_size * 1.3))

        compact = config.compact
        self.code_label.setVisible(not compact)
        self.sparkline.setVisible(config.show_sparkline and not compact)
        self._layout.setContentsMargins(12, 2 if compact else 4, 12, 2 if compact else 4)

    def update_quote(self, quote: Quote, config: Config) -> None:
        color = direction_color(config, quote.change)
        self.name_label.setText(quote.name or quote.symbol)
        self.code_label.setText(quote.symbol if quote.name != quote.symbol else "")

        if quote.error:
            self.price_label.setText(quote.error)
            self.price_label.setStyleSheet(_color_style(MUTED))
            self.change_label.setText("")
            self.sparkline.clear()
            self._set_dark(None, config)
            return

        self.price_label.setText(fmt_price(quote.price))
        self.price_label.setStyleSheet(_color_style(color))
        self.change_label.setText(fmt_change(quote.change, quote.change_percent))
        self.change_label.setStyleSheet(_color_style(color))
        self.sparkline.set_color(color)
        self.sparkline.push(quote.price)
        self._set_dark(quote.dark_fund, config)

    def _set_dark(self, dark_fund: float | None, config: Config) -> None:
        text = fmt_money(dark_fund) if config.show_dark_trade and not config.compact else None
        visible = text is not None
        self.dark_label.setVisible(visible)
        self.dark_value.setVisible(visible)
        if not visible:
            return
        self.dark_value.setText(text)
        self.dark_value.setStyleSheet(_color_style(direction_color(config, dark_fund)))
