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
        self._base_minimum_width = 120
        self._width_override: int | None = None
        self._width_override_in_size_hint = False
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
        self._base_minimum_width = 4 + price_width + gap + 40 + 5
        self.setMinimumWidth(self._base_minimum_width)
        self.setMaximumWidth(16777215)
        self.setMaximumHeight(16777215)
        self.updateGeometry()
        self.update()

    def set_width_override(
        self, width: int | None, *, use_size_hint: bool = False
    ) -> None:
        """冻结绘制宽度；仅被点击行可选择把该值暴露给 sizeHint。"""
        value = None if width is None else max(self._base_minimum_width, int(width))
        hint = bool(use_size_hint and value is not None)
        if value == self._width_override and hint == self._width_override_in_size_hint:
            return
        self._width_override = value
        self._width_override_in_size_hint = hint
        self.update()

    @property
    def has_width_override(self) -> bool:
        return self._width_override is not None

    @property
    def width_override(self) -> int | None:
        return self._width_override

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
        width = (
            self._width_override
            if self._width_override_in_size_hint and self._width_override is not None
            else self._preferred_width
        )
        return QSize(width, self._preferred_height)

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
        actual_width = float(self.width())
        visual_width = min(
            actual_width,
            float(self._width_override) if self._width_override is not None else actual_width,
        )
        # 展开详情时，未展开行仍占整行，但盘口只在屏幕外沿的原宽度区域绘制。
        content_left = 0.0 if mirror else actual_width - visual_width
        content_right = visual_width if mirror else actual_width
        axis_x = content_left + 5.0 if mirror else content_right - 5.0
        outer = 4.0
        gap = max(6.0, round(self._config.font_size * 0.45))
        price_width = max(66.0, round(self._config.stock_price_font_size * 4.6))
        if mirror:
            price_right = content_right - outer
            price_left = price_right - price_width
            depth_inner_x = price_left - gap
        else:
            price_left = content_left + outer
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
        max_bar = (
            max(1.0, depth_inner_x - axis_x)
            if mirror
            else max(1.0, axis_x - depth_inner_x)
        )

        ask_by_price: dict[float, float] = {}
        bid_by_price: dict[float, float] = {}
        for row in levels:
            if row.side == "ask" and row.price >= mid_price:
                ask_by_price[row.price] = ask_by_price.get(row.price, 0.0) + row.volume
            elif row.side == "bid" and row.price <= mid_price:
                bid_by_price[row.price] = bid_by_price.get(row.price, 0.0) + row.volume

        # 主窗口按档位顺序平均占满上下半区，不按绝对价差压缩。
        asks = sorted(ask_by_price.items(), key=lambda item: item[0])  # 近 -> 远
        bids = sorted(bid_by_price.items(), key=lambda item: item[0], reverse=True)  # 近 -> 远

        def distributed_y(index: int, count: int, *, upper: bool) -> int:
            near = center_y - 4.0 if upper else center_y + 4.0
            far = 2.0 if upper else float(self.height() - 2)
            if count <= 1:
                return round((near + far) / 2.0)
            ratio = index / (count - 1)
            return round(near + (far - near) * ratio)

        buckets: dict[tuple[int, str], float] = {}
        for index, (_price, volume) in enumerate(asks):
            y = distributed_y(index, len(asks), upper=True)
            buckets[(y, "ask")] = buckets.get((y, "ask"), 0.0) + volume
        for index, (_price, volume) in enumerate(bids):
            y = distributed_y(index, len(bids), upper=False)
            buckets[(y, "bid")] = buckets.get((y, "bid"), 0.0) + volume
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

    def _price_hit_rect(self) -> QRectF:
        """当前几何下整列股价点击热区；不依赖上一次 paintEvent。"""
        _mirror, _axis_x, _depth_inner_x, price_left, price_right = self._horizontal_geometry()
        return QRectF(
            price_left,
            0.0,
            max(0.0, price_right - price_left),
            float(self.height()),
        )

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

        # 点击热区扩成整个价格列。用户不需要精确点中文字，只要点到股价这一列
        # 就能展开；同时不侵入右侧盘口区域，避免误触。
        percent_height = max(16.0, self._config.stock_percent_font_size + 6.0)
        self._price_rect = self._price_hit_rect()

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
        if event.button() == Qt.LeftButton and self._price_hit_rect().contains(event.position()):
            self.price_clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
