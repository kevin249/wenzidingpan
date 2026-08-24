"""网格里的一格行情。

版式：左边名称压暗盘资金，中间当日分时图，右边现价压涨跌。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..config import Config
from ..intraday import Trend
from ..providers.base import Quote
from .sparkline import Sparkline
from .theme import MUTED, direction_color, fmt_change, fmt_money, fmt_price, make_font


def _color_style(color) -> str:
    return f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"


class QuoteRow(QWidget):
    def __init__(self, symbol: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.symbol = symbol

        self.name_label = QLabel()
        self.price_label = QLabel()
        self.change_label = QLabel()
        self.dark_label = QLabel("暗盘")
        self.dark_value = QLabel()
        self.sparkline = Sparkline()

        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.change_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dark_label.setStyleSheet(_color_style(MUTED))
        self.sparkline.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.dark_box = QWidget()
        dark_layout = QHBoxLayout(self.dark_box)
        dark_layout.setContentsMargins(0, 0, 0, 0)
        dark_layout.setSpacing(5)
        dark_layout.addWidget(self.dark_label)
        dark_layout.addWidget(self.dark_value)
        dark_layout.addStretch(1)

        layout = QGridLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(1)
        layout.addWidget(self.name_label, 0, 0)
        layout.addWidget(self.dark_box, 1, 0)
        layout.addWidget(self.sparkline, 0, 1, 2, 1)  # 走势图纵向占满整格
        layout.addWidget(self.price_label, 0, 2)
        layout.addWidget(self.change_label, 1, 2)
        layout.setColumnStretch(1, 1)  # 多出来的宽度都给走势图
        self._layout = layout

    # ------------------------------------------------------------ 外观

    def apply_config(self, config: Config) -> None:
        self.name_label.setFont(make_font(config, 0.95, bold=True))
        self.price_label.setFont(make_font(config, 1.15, bold=True))
        self.change_label.setFont(make_font(config, 0.85))
        self.dark_label.setFont(make_font(config, 0.75))
        self.dark_value.setFont(make_font(config, 0.75))

        compact = config.compact
        # 紧凑模式只省掉暗盘和页脚，走势图照画——只是压扁一点。
        self.sparkline.setVisible(config.show_sparkline)
        self.sparkline.setMinimumHeight(
            round(config.font_size * (1.6 if compact else 2.6)) if config.show_sparkline else 0
        )
        self.sparkline.setMinimumWidth(round(config.font_size * 4) if config.show_sparkline else 0)
        self._layout.setContentsMargins(12, 2 if compact else 5, 12, 2 if compact else 5)

    # ------------------------------------------------------------ 数据

    def update_quote(self, quote: Quote, config: Config, trend: Trend | None = None) -> None:
        color = direction_color(config, quote.change)
        self.name_label.setText(quote.name or quote.symbol)

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
        self.sparkline.push_sample(quote.price)
        self.sparkline.set_series(trend.prices if trend else [])
        # 分时接口给了昨收就用它的，否则用行情里的——虚线基准不能缺。
        self.sparkline.set_prev_close(
            (trend.prev_close if trend and trend.prev_close else None) or quote.prev_close
        )

        self._set_dark(quote.dark_fund, config)

    def _set_dark(self, dark_fund: float | None, config: Config) -> None:
        text = fmt_money(dark_fund) if config.show_dark_trade and not config.compact else None
        self.dark_box.setVisible(text is not None)
        if text is None:
            return
        self.dark_value.setText(text)
        self.dark_value.setStyleSheet(_color_style(direction_color(config, dark_fund)))
