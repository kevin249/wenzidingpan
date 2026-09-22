"""主题2：外侧股价 + 内侧千档，支持左右镜像。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..config import Config
from ..mcp_depth import DEPTH_FIVE, DEPTH_FULL, DEPTH_TEN, DepthSnapshot
from .theme import MUTED, make_font

BID_COLOR = QColor(240, 79, 90)
ASK_COLOR = QColor(34, 197, 94)


class DepthLadder(QWidget):
    """主题2紧凑盘口。

    靠右时从左到右为：股价/涨跌幅 -> 虚线 -> 挂单 -> 最右价格轴。
    靠左时完整镜像。上下方向始终是上方绿色卖盘、下方红色买盘。
    """

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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(52)

    def apply_config(self, config: Config) -> None:
        self._config = config
        price_width = max(66, round(config.stock_price_font_size * 4.6))
        gap = max(6, round(config.font_size * 0.45))
        # 总宽度 = 外侧股价区 + 间隔 + 用户指定盘口宽度 + 价格轴边距。
        self._preferred_width = 4 + price_width + gap + config.theme2_depth_width + 5
        self._preferred_height = max(88, round(config.font_size * 7.2))
        # 配置宽度只决定默认/自然尺寸；用户拖右下角后，实际宽高由父布局分配。
        self.setMinimumWidth(4 + price_width + gap + 40 + 5)
        self.setMaximumWidth(16777215)
        self.setMaximumHeight(16777215)
        self.updateGeometry()
        self.update()

    def set_depth(self, snapshot: DepthSnapshot | None) -> None:
        if snapshot != self._depth:
            self._depth = snapshot
            mode = {
                DEPTH_FULL: "千档",
                DEPTH_TEN: "十档（千档5秒重试中）",
                DEPTH_FIVE: "五档（千档5秒重试中）",
            }.get(snapshot.depth_mode if snapshot else "", "盘口不可用")
            self.setToolTip(f"盘口：{mode}")
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

    def _book_mid_price(self, levels) -> float | None:
        if self._price is not None and self._price > 0:
            return self._price
        bids = [row.price for row in levels if row.side == "bid" and row.price > 0]
        asks = [row.price for row in levels if row.side == "ask" and row.price > 0]
        if bids and asks:
            return (max(bids) + min(asks)) / 2.0
        return max(bids) if bids else min(asks) if asks else None

    def _horizontal_geometry(self) -> tuple[bool, float, float, float, float]:
        """返回 mirror, axis_x, depth_inner_x, price_left, price_right。

        depth_inner_x 是盘口靠近股价一侧的边界；挂单只能在它与价格轴之间出现。
        """
        mirror = self._config.theme2_side == "left"
        axis_x = 5.0 if mirror else float(self.width() - 5)
        outer = 4.0
        gap = max(6.0, round(self._config.font_size * 0.45))
        price_width = max(66.0, round(self._config.stock_price_font_size * 4.6))
        # 实际窗口宽度优先：右下角拖宽/拖窄时盘口自动吃掉剩余空间。
        # theme2_depth_width 仅作为 sizeHint 的默认盘口宽度。
        if mirror:
            price_right = float(self.width()) - outer
            price_left = price_right - price_width
            depth_inner_x = price_left - gap
        else:
            price_left = outer
            price_right = price_left + price_width
            depth_inner_x = price_right + gap
        return mirror, axis_x, depth_inner_x, price_left, price_right

    def _draw_depth(self, painter: QPainter) -> None:
        snapshot = self._depth
        if snapshot is None or not snapshot.available or not snapshot.levels:
            return

        levels = [row for row in snapshot.levels if row.price > 0 and row.volume > 0]
        if not levels:
            return
        mid_price = self._book_mid_price(levels)
        if mid_price is None or mid_price <= 0:
            return

        mirror, axis_x, depth_inner_x, _, _ = self._horizontal_geometry()
        center_y = self.height() / 2.0
        upper_span = max(1.0, center_y - 6.0)
        lower_span = max(1.0, self.height() - center_y - 6.0)
        max_bar = (
            max(1.0, depth_inner_x - axis_x)
            if mirror
            else max(1.0, axis_x - depth_inner_x)
        )

        asks = [row for row in levels if row.side == "ask" and row.price >= mid_price]
        bids = [row for row in levels if row.side == "bid" and row.price <= mid_price]
        ask_span = max((row.price - mid_price for row in asks), default=0.0)
        bid_span = max((mid_price - row.price for row in bids), default=0.0)
        ask_span = ask_span or max(abs(mid_price) * 0.001, 0.01)
        bid_span = bid_span or max(abs(mid_price) * 0.001, 0.01)

        buckets: dict[tuple[int, str], float] = {}
        for row in asks:
            ratio = min(max((row.price - mid_price) / ask_span, 0.0), 1.0)
            y = max(2, round(center_y - 3.0 - ratio * upper_span))
            buckets[(y, "ask")] = buckets.get((y, "ask"), 0.0) + row.volume
        for row in bids:
            ratio = min(max((mid_price - row.price) / bid_span, 0.0), 1.0)
            y = min(self.height() - 2, round(center_y + 3.0 + ratio * lower_span))
            buckets[(y, "bid")] = buckets.get((y, "bid"), 0.0) + row.volume
        if not buckets:
            return

        maximum = max(buckets.values()) or 1.0
        axis_color = QColor(130, 136, 150)
        axis_color.setAlpha(135)
        painter.setPen(QPen(axis_color, 1.0))
        painter.drawLine(round(axis_x), 2, round(axis_x), self.height() - 2)

        for (y, side), volume in buckets.items():
            length = max(1.0, max_bar * volume / maximum)
            if self._config.grayscale:
                color = QColor(145, 145, 145) if side == "bid" else QColor(95, 95, 95)
            else:
                color = QColor(BID_COLOR if side == "bid" else ASK_COLOR)
            color.setAlpha(112)
            painter.setPen(QPen(color, 1.2))
            if mirror:
                painter.drawLine(round(axis_x), y, round(axis_x + length), y)
            else:
                painter.drawLine(round(axis_x - length), y, round(axis_x), y)

    def _draw_price_and_guide(self, painter: QPainter) -> None:
        mirror, axis_x, _, price_left, price_right = self._horizontal_geometry()
        center_y = self.height() / 2.0

        price_text = "--" if self._price is None else f"{self._price:.2f}"
        percent_text = (
            "--"
            if self._percent is None
            else f"{'+' if self._percent > 0 else ''}{self._percent:.2f}%"
        )

        price_font = make_font(
            self._config,
            bold=True,
            pixel_size=max(10, self._config.stock_price_font_size),
        )
        price_metrics = QFontMetricsF(price_font)
        price_text_width = min(price_right - price_left, price_metrics.horizontalAdvance(price_text) + 4)
        price_height = max(20.0, price_metrics.height() + 2.0)

        if mirror:
            price_text_rect = QRectF(
                price_right - price_text_width,
                center_y - price_height / 2.0,
                price_text_width,
                price_height,
            )
            guide_start = axis_x
            guide_end = price_text_rect.left() - 3.0
            price_align = Qt.AlignRight | Qt.AlignVCenter
        else:
            price_text_rect = QRectF(
                price_left,
                center_y - price_height / 2.0,
                price_text_width,
                price_height,
            )
            guide_start = price_text_rect.right() + 3.0
            guide_end = axis_x
            price_align = Qt.AlignLeft | Qt.AlignVCenter

        # 当前价文字的垂直中点就是买卖交界；虚线从文字边缘一直指到价格轴。
        guide_color = QColor(185, 191, 204)
        guide_color.setAlpha(165)
        guide_pen = QPen(guide_color, 1.0, Qt.DashLine)
        painter.setPen(guide_pen)
        painter.drawLine(
            round(min(guide_start, guide_end)),
            round(center_y),
            round(max(guide_start, guide_end)),
            round(center_y),
        )
        painter.setBrush(guide_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            QRectF(axis_x - 1.8, center_y - 1.8, 3.6, 3.6)
        )

        # 点击热区覆盖当前价与涨跌幅，但不再用中间悬浮卡片遮住盘口。
        percent_height = max(16.0, self._config.stock_percent_font_size + 6.0)
        panel_top = price_text_rect.top() - 2.0
        panel_bottom = min(
            float(self.height() - 2),
            price_text_rect.bottom() + percent_height + 4.0,
        )
        self._price_rect = QRectF(
            price_left,
            panel_top,
            price_right - price_left,
            panel_bottom - panel_top,
        )

        if self._error:
            painter.setPen(QColor(MUTED))
            painter.setFont(
                make_font(
                    self._config,
                    pixel_size=max(8, self._config.stock_percent_font_size),
                )
            )
            painter.drawText(self._price_rect, price_align, self._error[:18])
            return

        painter.setPen(self._price_color)
        painter.setFont(price_font)
        painter.drawText(price_text_rect, price_align, price_text)

        percent_rect = QRectF(
            price_left,
            price_text_rect.bottom() + 1.0,
            price_right - price_left,
            percent_height,
        )
        painter.setFont(
            make_font(
                self._config,
                bold=self._config.stock_percent_bold,
                pixel_size=max(8, self._config.stock_percent_font_size),
            )
        )
        painter.drawText(percent_rect, price_align, percent_text)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._draw_depth(painter)
        self._draw_price_and_guide(painter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._price_rect.contains(event.position()):
            self.price_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
