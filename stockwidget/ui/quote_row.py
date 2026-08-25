"""网格里的一格行情。

两种版式，中间永远是当日分时图：

* 左中右（``row_style="sides"``）：左边名称压暗盘资金，右边现价压涨跌幅，左右各两行；
* 上中下（``row_style="stacked"``）：上面名称与现价同一行，下面暗盘与涨跌幅同一行。

选左中右时，格子窄到三列放不下才会临时退回上中下，避免文字被裁掉。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..config import Config
from ..intraday import Trend, calculate_bs_points
from ..providers.base import Quote
from .sparkline import Sparkline
from .theme import (
    BLACK,
    MUTED,
    configured_text_color,
    direction_color,
    fmt_money,
    fmt_price,
    make_font,
)


MAX_WIDGET_SIZE = 16777215  # QWIDGETSIZE_MAX：解除之前设过的高度上限


def _color_style(color) -> str:
    return f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"


class QuoteRow(QWidget):
    def __init__(self, symbol: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self._last_quote: Quote | None = None
        self._last_trend: Trend | None = None

        self.name_label = QLabel()
        self.price_label = QLabel()
        self.percent_label = QLabel()
        self.dark_label = QLabel("暗")
        self.dark_value = QLabel()
        self.sparkline = Sparkline()
        self._config = Config()
        self._narrow = False
        self._layout_state: tuple[bool, bool, bool, bool, bool] | None = None

        self.price_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.dark_label.setStyleSheet(_color_style(BLACK))
        self.sparkline.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # 父网格必须能把每只股票压进外框，内部再切换成窄卡片布局。
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setMinimumSize(0, 0)

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
        layout.addWidget(self.percent_label, 1, 2)
        layout.setColumnStretch(1, 1)  # 多出来的宽度都给走势图
        self._layout = layout
        self._dark_layout = dark_layout

    # ------------------------------------------------------------ 外观

    def apply_config(self, config: Config) -> None:
        self._config = config
        compact = config.compact
        # 紧凑模式只省掉暗盘和页脚，走势图照画——只是压扁一点。
        self.sparkline.setVisible(config.show_sparkline)
        self.name_label.setVisible(config.show_stock_name)
        self.price_label.setVisible(config.show_stock_price)
        self.percent_label.setVisible(config.show_stock_price)
        self.sparkline.set_annotation_options(
            show_signals=config.show_bs_points,
            show_open_line=config.show_open_line,
            show_high_low=config.show_high_low,
            show_fill=config.show_sparkline_fill,
            grayscale=config.grayscale,
        )
        self.sparkline.set_annotation_font(
            make_font(config, pixel_size=config.chart_label_font_size)
        )
        self._update_layout_mode()
        if self._last_quote is not None:
            self.update_quote(self._last_quote, config, self._last_trend)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._update_layout_mode()

    def _update_layout_mode(self) -> None:
        """按所选版式摆放文字；各类字体保持用户设置的比例。"""
        config = self._config
        side_font = max(config.stock_name_font_size, config.stock_price_font_size)
        narrow = self.width() < max(120, round(side_font * 12))
        self.name_label.setFont(
            make_font(
                config,
                bold=config.stock_name_bold,
                pixel_size=config.stock_name_font_size,
            )
        )
        self.price_label.setFont(
            make_font(
                config,
                bold=config.stock_price_bold,
                pixel_size=config.stock_price_font_size,
            )
        )
        self.percent_label.setFont(
            make_font(
                config,
                bold=config.stock_percent_bold,
                pixel_size=config.stock_percent_font_size,
            )
        )
        self.dark_label.setFont(
            make_font(
                config,
                bold=config.dark_trade_bold,
                pixel_size=config.dark_trade_font_size,
            )
        )
        self.dark_value.setFont(
            make_font(
                config,
                bold=config.dark_trade_bold,
                pixel_size=config.dark_trade_font_size,
            )
        )
        show_chart = config.show_sparkline
        # 用户设了 K 线高度就锁死走势图；留 0 时仍按字号推算并可随格子拉伸。
        fixed_chart = config.chart_height > 0
        compact_padding = max(2, round(config.font_size * 0.35))
        compact_gap = max(2, round(config.font_size * 0.25))
        # 用户选上中下就一直上中下；选左中右时，只有窄到三列排不开才临时退回来。
        prefer_stacked = config.row_style == "stacked"
        stacked = prefer_stacked or narrow
        # 上中下要求上下两行各自同行，所以显式选它时不再往下拆行。
        stacked_sides = (
            not prefer_stacked
            and narrow
            and self.width() < 180
            and config.show_stock_name
            and config.show_stock_price
            and self.name_label.minimumSizeHint().width()
            + self.price_label.minimumSizeHint().width()
            + compact_padding * 2
            + compact_gap
            > self.width() - max(8, compact_gap * 2)
        )
        stacked_secondary = (
            not prefer_stacked
            and narrow
            and self.width() < 180
            and not self.dark_box.isHidden()
            and config.show_stock_price
            and self.dark_label.minimumSizeHint().width()
            + self.dark_value.minimumSizeHint().width()
            + self._dark_layout.spacing()
            + self.percent_label.minimumSizeHint().width()
            + compact_padding * 2
            + compact_gap
            > self.width() - max(8, compact_gap * 2)
        )
        layout_state = (stacked, show_chart, stacked_sides, stacked_secondary, fixed_chart)
        # 固定高度的走势图在格子里垂直居中；自动高度则铺满，交给布局拉伸。
        chart_align = Qt.AlignVCenter if fixed_chart else Qt.AlignmentFlag(0)
        if layout_state != self._layout_state:
            for widget in (
                self.name_label,
                self.price_label,
                self.percent_label,
                self.dark_box,
                self.sparkline,
            ):
                self._layout.removeWidget(widget)
            if stacked:
                if stacked_sides:
                    self._layout.addWidget(self.name_label, 0, 0, 1, 2)
                    self._layout.addWidget(self.price_label, 1, 0, 1, 2)
                    chart_row = 2
                else:
                    self._layout.addWidget(self.name_label, 0, 0)
                    self._layout.addWidget(self.price_label, 0, 1)
                    chart_row = 1
                if show_chart:
                    self._layout.addWidget(self.sparkline, chart_row, 0, 1, 2, chart_align)
                    details_row = chart_row + 1
                else:
                    details_row = chart_row
                if stacked_secondary:
                    self._layout.addWidget(
                        self.dark_box, details_row, 0, 1, 2, Qt.AlignLeft | Qt.AlignVCenter
                    )
                    self._layout.addWidget(self.percent_label, details_row + 1, 0, 1, 2)
                else:
                    self._layout.addWidget(
                        self.dark_box, details_row, 0, Qt.AlignLeft | Qt.AlignVCenter
                    )
                    self._layout.addWidget(self.percent_label, details_row, 1)
                self._layout.setColumnStretch(0, 1)
                # 右列按现价/涨跌幅的自然宽度分配，避免等分后文字被裁切。
                self._layout.setColumnStretch(1, 0 if not stacked_sides else 1)
                self._layout.setColumnStretch(2, 0)
            else:
                self._layout.addWidget(self.name_label, 0, 0)
                self._layout.addWidget(self.dark_box, 1, 0, Qt.AlignLeft | Qt.AlignVCenter)
                if show_chart:
                    self._layout.addWidget(self.sparkline, 0, 1, 2, 1, chart_align)
                    self._layout.addWidget(self.price_label, 0, 2)
                    self._layout.addWidget(self.percent_label, 1, 2)
                    self._layout.setColumnStretch(0, 0)
                    self._layout.setColumnStretch(1, 1)
                    self._layout.setColumnStretch(2, 0)
                else:
                    self._layout.addWidget(self.price_label, 0, 1)
                    self._layout.addWidget(self.percent_label, 1, 1)
                    # 多余宽度放在内容右侧，中间不再保留走势图空位。
                    self._layout.setColumnStretch(0, 0)
                    self._layout.setColumnStretch(1, 0)
                    self._layout.setColumnStretch(2, 1)
            self._layout_state = layout_state
        self._narrow = narrow

        compact = config.compact
        if narrow:
            padding = max(2, round(config.font_size * 0.35))
            gap = max(2, round(config.font_size * 0.25))
            chart_height = round(config.font_size * (1.1 if compact else 1.5))
            chart_width = 0
        else:
            padding = max(4, round(config.font_size * 0.65))
            gap = max(4, round(config.font_size * 0.6))
            chart_height = round(config.font_size * (1.6 if compact else 2.6))
            chart_width = round(config.font_size * 4)
        auto_height = chart_height
        if fixed_chart:
            chart_height = config.chart_height
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setHorizontalSpacing(gap)
        self._layout.setVerticalSpacing(1)
        self._dark_layout.setSpacing(max(2, gap // 2))
        self.sparkline.setMinimumWidth(chart_width if config.show_sparkline else 0)
        if not config.show_sparkline:
            self.sparkline.set_preferred_height(0)
            self.sparkline.setMinimumHeight(0)
            self.sparkline.setMaximumHeight(MAX_WIDGET_SIZE)
            return
        # 想要的高度走 sizeHint，硬下限只留字号推算出的那点高度：窗口塞不下时
        # （屏幕不够高、或用户把外框拖小）走势图先被压扁，而不是把行顶出可视区。
        self.sparkline.set_preferred_height(chart_height)
        self.sparkline.setMinimumHeight(min(chart_height, auto_height))
        # 自动模式下走势图仍可随格子拉伸，只有显式设了高度才封顶。
        self.sparkline.setMaximumHeight(chart_height if fixed_chart else MAX_WIDGET_SIZE)

    # ------------------------------------------------------------ 数据

    def update_quote(self, quote: Quote, config: Config, trend: Trend | None = None) -> None:
        self._last_quote = quote
        self._last_trend = trend
        color = direction_color(config, quote.change)
        self.name_label.setText(quote.name or quote.symbol)
        self.name_label.setStyleSheet(
            _color_style(configured_text_color(config.stock_name_color, color))
        )

        if quote.error:
            self.price_label.setText(quote.error)
            self.price_label.setStyleSheet(_color_style(MUTED))
            self.percent_label.setText("")
            self.sparkline.clear()
            self._set_dark(None, config)
            return

        self.price_label.setText(fmt_price(quote.price))
        self.price_label.setStyleSheet(
            _color_style(configured_text_color(config.stock_price_color, color))
        )
        percent_sign = "+" if quote.change_percent is not None and quote.change_percent > 0 else ""
        self.percent_label.setText(
            f"{percent_sign}{quote.change_percent:.2f}%"
            if quote.change_percent is not None
            else ""
        )
        self.percent_label.setStyleSheet(
            _color_style(configured_text_color(config.stock_percent_color, color))
        )

        self.sparkline.set_color(color)
        self.sparkline.push_sample(quote.price)
        self.sparkline.set_series(trend.prices if trend else [])
        # 分时接口给了昨收就用它的，否则用行情里的——虚线基准不能缺。
        self.sparkline.set_prev_close(
            (trend.prev_close if trend and trend.prev_close else None) or quote.prev_close
        )
        prices = trend.prices if trend else self.sparkline.points
        self.sparkline.set_annotations(
            trend.open_price if trend else (prices[0] if prices else None),
            calculate_bs_points(prices),
            show_signals=config.show_bs_points,
            show_open_line=config.show_open_line,
            show_high_low=config.show_high_low,
            show_fill=config.show_sparkline_fill,
            grayscale=config.grayscale,
        )

        self._set_dark(quote.dark_fund, config)
        # 文本写入后 minimumSizeHint 才准确；必要时切到更窄的堆叠布局。
        self._update_layout_mode()

    def _set_dark(self, dark_fund: float | None, config: Config) -> None:
        text = fmt_money(dark_fund) if config.show_dark_trade and not config.compact else None
        dark_color = configured_text_color(
            config.dark_trade_color,
            direction_color(config, dark_fund),
        )
        style = _color_style(dark_color)
        self.dark_label.setStyleSheet(style)
        self.dark_value.setStyleSheet(style)
        self.dark_box.setVisible(text is not None)
        if text is None:
            return
        self.dark_value.setText(text)
