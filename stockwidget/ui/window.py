"""桌面悬浮窗：无边框、半透明、可最前显示、可拖拽。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import Bounds, Config
from ..poller import Snapshot
from .marquee import Marquee
from .quote_row import QuoteRow
from .theme import BORDER, MUTED, TEXT, make_font

# 缩放基准：窗口宽度相对它的比例，就是字号与图高的放大倍数。
REFERENCE_WIDTH = 300
HANDLE_SIZE = 20


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))

BUTTON_STYLE = """
QPushButton { border: none; border-radius: 6px; background: transparent; color: #8b93a7; }
QPushButton:hover { background: rgba(255,255,255,0.08); color: #e8eaf0; }
"""
QUIT_BUTTON_STYLE = BUTTON_STYLE + """
QPushButton:hover { background: rgba(240,79,90,0.18); color: #f04f5a; }
"""


class TitleBar(QWidget):
    """标题栏：显示状态，同时是窗口的拖拽把手。"""

    refresh_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    grayscale_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None

        self.brand = QLabel("行情")
        self.status = QLabel("连接中…")
        self.brand.setStyleSheet("color: #8b93a7;")
        self.status.setStyleSheet("color: #8b93a7;")

        self.refresh_button = self._button("⟳", "立即刷新", self.refresh_requested)
        self.settings_button = self._button("⚙", "在浏览器中打开设置", self.settings_requested)
        self.grayscale_button = self._button("灰", "切换彩色 / 灰度显示", self.grayscale_requested)
        self.quit_button = self._button("✕", "退出", self.quit_requested, QUIT_BUTTON_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(8)
        layout.addWidget(self.brand)
        layout.addWidget(self.status, 1)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.grayscale_button)
        layout.addWidget(self.quit_button)

    def _button(self, text: str, tip: str, signal: Signal, style: str = BUTTON_STYLE) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(style)
        button.clicked.connect(signal.emit)
        return button

    def apply_config(self, config: Config) -> None:
        self.brand.setFont(make_font(config, 0.9, bold=True))
        self.status.setFont(make_font(config, 0.82))
        self.grayscale_button.setText("彩" if config.grayscale else "灰")
        for button in (self.refresh_button, self.settings_button, self.grayscale_button, self.quit_button):
            button.setFont(make_font(config, 0.95))
            button.setFixedSize(round(config.font_size * 1.7), round(config.font_size * 1.7))

    def set_status(self, text: str, error: bool = False) -> None:
        color = "#f04f5a" if error else "#8b93a7"
        self.status.setStyleSheet(f"color: {color};")
        self.status.setText(text)

    # 无边框窗口没有系统标题栏，拖拽要自己实现。
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._drag_offset = None


class ResizeGrip(QWidget):
    """右下角的缩放把手。

    刻意不用 ``QSizeGrip``：缩放要联动字号，而字号变大又会把控件的最小宽度顶大、
    反过来撑宽窗口，挂在 ``resizeEvent`` 上就成了正反馈，一路顶到上限。
    自己接管拖拽，就能只在「用户真的在拖」的时候改缩放。
    """

    dragged = Signal(QSize)  # 拖出来的新窗口尺寸

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("拖动缩放，字体与走势图会等比放大")
        self._origin: QPoint | None = None
        self._start_size = QSize()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setPen(QPen(MUTED, 1.1))
        for offset in (3, 7, 11):
            painter.drawLine(QPointF(offset, 12), QPointF(12, offset))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._start_size = self.window().size()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._origin is None or not event.buttons() & Qt.LeftButton:
            return
        delta = event.globalPosition().toPoint() - self._origin
        self.dragged.emit(
            QSize(
                max(180, self._start_size.width() + delta.x()),
                max(80, self._start_size.height() + delta.y()),
            )
        )
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._origin = None
        event.accept()


class DragHandle(QWidget):
    """鼠标穿透时唯一还接收鼠标的东西：左上角一个可拖动的小把手。

    主窗口开了 ``WindowTransparentForInput`` 之后连自己都点不到了，
    所以把手必须是另一个独立窗口，跟着主窗口走。
    """

    moved = Signal(QPoint)

    def __init__(self) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(HANDLE_SIZE, HANDLE_SIZE)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("拖动移动组件（鼠标穿透已开启）")
        self._offset: QPoint | None = None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(17, 20, 28, 200))
        painter.drawRoundedRect(QRectF(0, 0, HANDLE_SIZE, HANDLE_SIZE), 6, 6)

        # 四向箭头，示意这里可以拖
        pen = QPen(TEXT, 1.2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        mid = HANDLE_SIZE / 2
        arm = HANDLE_SIZE * 0.28
        painter.drawLine(QPointF(mid, mid - arm), QPointF(mid, mid + arm))
        painter.drawLine(QPointF(mid - arm, mid), QPointF(mid + arm, mid))
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            tip = QPointF(mid + dx * arm, mid + dy * arm)
            wing = HANDLE_SIZE * 0.12
            if dx:
                painter.drawLine(tip, QPointF(tip.x() - dx * wing, tip.y() - wing))
                painter.drawLine(tip, QPointF(tip.x() - dx * wing, tip.y() + wing))
            else:
                painter.drawLine(tip, QPointF(tip.x() - wing, tip.y() - dy * wing))
                painter.drawLine(tip, QPointF(tip.x() + wing, tip.y() - dy * wing))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.LeftButton:
            self._offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            self.moved.emit(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._offset = None


class TickerWindow(QWidget):
    """组件主窗口。"""

    refresh_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    bounds_changed = Signal(object)
    grayscale_requested = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._rows: dict[str, QuoteRow] = {}
        self._scale = 1.0

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("股票行情组件")

        self.handle = DragHandle()
        self.handle.moved.connect(self._on_handle_moved)

        self.title_bar = TitleBar(self)
        self.title_bar.refresh_requested.connect(self.refresh_requested.emit)
        self.title_bar.settings_requested.connect(self.settings_requested.emit)
        self.title_bar.quit_requested.connect(self.quit_requested.emit)
        self.title_bar.grayscale_requested.connect(self.grayscale_requested.emit)

        # 自选按「设置里的行数」铺成网格：1 行就全部横向排开，2 行就铺两行。
        self.rows_host = QWidget()
        self.rows_layout = QGridLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 3, 0, 3)
        self.rows_layout.setSpacing(0)
        self._grid_shape: tuple[int, int] = (0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea, QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar:horizontal { height: 6px; background: transparent; }"
            "QScrollBar::handle { background: rgba(255,255,255,0.14); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }"
        )

        self.marquee = Marquee()
        self.empty_label = QLabel("自选列表为空\n点击 ⚙ 在浏览器里添加代码")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #8b93a7;")

        self.updated_label = QLabel("—")
        self.updated_label.setStyleSheet("color: #8b93a7;")
        self.updated_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.footer = QWidget()
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(12, 2, 4, 2)
        footer_layout.addWidget(self.updated_label, 1)
        self.grip = ResizeGrip(self.footer)
        self.grip.dragged.connect(self._on_grip_dragged)
        footer_layout.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.marquee)
        layout.addWidget(self.footer)

        # 移动和缩放都很频繁，攒一下再写盘。
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._emit_bounds)

        # 拖动过程中每一像素都重排字体太重，等手停下来再统一缩放。
        self._scale_timer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(80)
        self._scale_timer.timeout.connect(self._apply_scale)

        self.apply_config(config)

    # ------------------------------------------------------------ 外观

    def scaled_config(self) -> Config:
        """把配置里的基准字号乘上窗口缩放系数，界面所有尺寸都由它推出来。"""
        if abs(self._scale - 1.0) < 0.01:
            return self._config
        size = int(round(_clamp(self._config.font_size * self._scale, 8, 48)))
        return replace(self._config, font_size=size)

    def apply_config(self, config: Config) -> None:
        previous = self._config
        self._config = config

        flags = Qt.FramelessWindowHint | Qt.Tool
        if config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if config.click_through:
            # 让整窗不吃鼠标事件，点击直接落到下面的程序上。
            flags |= Qt.WindowTransparentForInput
        if flags != self.windowFlags():
            visible = self.isVisible()
            self.setWindowFlags(flags)
            if visible:
                self.show()  # 改 flag 会隐藏窗口，需要重新显示

        scaled = self.scaled_config()
        self.setWindowOpacity(config.opacity)
        self.title_bar.apply_config(scaled)
        self.marquee.apply_config(scaled)
        self.updated_label.setFont(make_font(scaled, 0.78))
        self.empty_label.setFont(make_font(scaled, 0.9))
        for row in self._rows.values():
            row.apply_config(scaled)
        if self._rows:
            # 字号会改变每个行情格的 minimumSizeHint，随配置一起刷新内容宽度。
            self._lay_out_grid(list(self._rows))

        single = config.layout == "single"
        self.marquee.setVisible(single)
        self.scroll.setVisible(not single and bool(self._rows))
        self.empty_label.setVisible(not single and not self._rows)
        self.footer.setVisible(not single and not config.compact)

        # 穿透时整窗不可交互，只留左上角把手能拖。
        self.handle.setVisible(config.click_through and self.isVisible())
        self._move_handle()

        if (previous.color_scheme, previous.background_color, previous.background_alpha) != (
            config.color_scheme,
            config.background_color,
            config.background_alpha,
        ):
            self.update()
        self._resize_to_grid()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        background = QColor(self._config.background_color)
        background.setAlphaF(self._config.background_alpha)
        painter.fillPath(path, background)
        if self._config.background_alpha > 0.02:  # 全透明时连边框也不该留
            painter.setPen(QPen(BORDER, 1))
            painter.drawPath(path)

    # ------------------------------------------------------------ 数据

    def update_snapshot(self, snapshot: Snapshot, provider_label: str) -> None:
        quotes = snapshot.quotes
        if self._config.layout == "single":
            self.marquee.set_quotes(quotes)
        else:
            self._sync_rows(quotes, snapshot.trends)

        failed = sum(1 for q in quotes if q.error)
        parts = [provider_label]
        if failed:
            parts.append(f"{failed} 个代码取数失败")
        if snapshot.dark_enabled:
            if snapshot.dark_error:
                parts.append(f"暗盘取数失败：{snapshot.dark_error}")
            elif snapshot.dark_date:
                parts.append(f"暗盘 {snapshot.dark_date}")
        self.title_bar.set_status(" · ".join(parts), error=bool(failed))

        self.updated_label.setText(
            f"更新于 {datetime.fromtimestamp(snapshot.at):%H:%M:%S} · 每 {self._config.refresh_seconds} 秒"
        )

    def _sync_rows(self, quotes, trends: dict | None = None) -> None:
        trends = trends or {}
        scaled = self.scaled_config()
        for index, quote in enumerate(quotes):
            row = self._rows.get(quote.symbol)
            if row is None:
                row = QuoteRow(quote.symbol)
                row.apply_config(scaled)
                self._rows[quote.symbol] = row
            row.update_quote(quote, scaled, trends.get(quote.symbol))

        wanted = {q.symbol for q in quotes}
        for symbol in list(self._rows):
            if symbol not in wanted:
                row = self._rows.pop(symbol)
                self.rows_layout.removeWidget(row)
                row.deleteLater()

        self._lay_out_grid([q.symbol for q in quotes])

        single = self._config.layout == "single"
        self.scroll.setVisible(not single and bool(self._rows))
        self.empty_label.setVisible(not single and not self._rows)
        self._resize_to_grid()

    def _grid_size(self, count: int) -> tuple[int, int]:
        """设置里的行数决定网格有几行，列数由自选数量摊出来。"""
        rows = max(1, min(self._config.visible_rows, max(count, 1)))
        columns = max(1, -(-count // rows))  # 向上取整
        return rows, columns

    def _lay_out_grid(self, order: list[str]) -> None:
        """按自选顺序从左到右填，填满一行再换下一行。"""
        rows, columns = self._grid_size(len(order))
        # QScrollArea 开启 widgetResizable 后会默认把内容压到视口宽度。单行平铺时
        # 这会让每格一起缩水，最先被挤掉的正是中间的 mini K 线。明确保留每格的
        # 最小宽度，超过屏幕的部分交给横向滚动条，而不是裁图。
        cell_width = max(
            (self._rows[s].minimumSizeHint().width() for s in order if s in self._rows),
            default=0,
        )
        self.rows_host.setMinimumWidth(cell_width * columns)
        if (rows, columns) == self._grid_shape and all(
            self.rows_layout.indexOf(self._rows[s]) >= 0 for s in order if s in self._rows
        ):
            return  # 形状没变，位置也都在，不必重排

        for symbol in order:
            row = self._rows.get(symbol)
            if row is not None:
                self.rows_layout.removeWidget(row)
        for index, symbol in enumerate(order):
            row = self._rows.get(symbol)
            if row is not None:
                self.rows_layout.addWidget(row, index // columns, index % columns)
        old_columns = self._grid_shape[1]
        for column in range(columns, old_columns):
            self.rows_layout.setColumnStretch(column, 0)
        for column in range(columns):
            self.rows_layout.setColumnStretch(column, 1)
        self._grid_shape = (rows, columns)

    # ------------------------------------------------------------ 尺寸

    def _resize_to_grid(self) -> None:
        """高度按网格行数算，宽度按列数摊开——1 行就是全部横向铺满。"""
        chrome = self.title_bar.sizeHint().height() + 2
        if self._config.layout == "single":
            self.setFixedHeight(chrome + self.marquee.height() + 4)
            self.setMinimumWidth(220)
            self.setMaximumWidth(16777215)
            return

        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        if not self._rows:
            self.resize(self.width(), chrome + 90)
            return

        sample = next(iter(self._rows.values()))
        rows, columns = self._grid_size(len(self._rows))
        footer = self.footer.sizeHint().height() if self.footer.isVisible() else 0
        height = chrome + sample.sizeHint().height() * rows + footer + 8

        # 宽度直接问网格要：自己按 sizeHint×列数 估会漏掉边距和滚动条，
        # 差那十几像素就会把最右一列的价格裁掉。
        self.rows_layout.activate()
        width = max(
            self.rows_host.sizeHint().width() + 10,
            columns * round(self.scaled_config().font_size * 13),
        )
        screen = self.screen()
        if screen is not None:  # 列太多时别撑出屏幕，剩下的交给横向滚动
            width = min(width, screen.availableGeometry().width())
        if self.rows_host.minimumWidth() > width:
            # 横向滚动条占用视口高度；把它预留出来，避免 mini K 线底部被遮住。
            height += self.scroll.horizontalScrollBar().sizeHint().height()
        self.resize(width, height)

    def restore_bounds(self, bounds: Bounds | None, available: list) -> None:
        """恢复上次位置前先确认它仍落在某块屏幕上（外接显示器可能已拔掉）。"""
        if bounds is None:
            self.resize(300, 260)
            return
        rect = (bounds.x, bounds.y, bounds.width, bounds.height)
        visible = any(
            rect[0] + rect[2] > geo.x()
            and rect[1] + rect[3] > geo.y()
            and rect[0] < geo.x() + geo.width()
            and rect[1] < geo.y() + geo.height()
            for geo in available
        )
        self.resize(bounds.width, bounds.height)
        # 上次拖成多宽，字号就该按同样比例恢复。
        self._scale = _clamp(bounds.width / REFERENCE_WIDTH, 0.6, 3.0)
        if visible:
            self.move(bounds.x, bounds.y)

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()
        self._move_handle()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()
        self._move_handle()

    def _on_grip_dragged(self, size: QSize) -> None:
        """只有用户真的在拖把手时才改缩放，避免布局回弹造成正反馈。"""
        self.resize(size)
        scale = _clamp(size.width() / REFERENCE_WIDTH, 0.6, 3.0)
        if abs(scale - self._scale) > 0.02:
            self._scale = scale
            self._scale_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.handle.setVisible(self._config.click_through)
        self._move_handle()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.handle.hide()

    def _apply_scale(self) -> None:
        """窗口拉大拉小之后，按新的比例把字号和图高重新推一遍。"""
        # apply_config 会按内容重算默认尺寸。拖拽是用户的明确选择，
        # 应在更新字号后恢复，否则鼠标一停窗口就会弹回去。
        dragged_size = self.size()
        self.apply_config(self._config)
        self.resize(dragged_size)

    def _move_handle(self) -> None:
        """把手贴在窗口左上角外沿，不挡住标题栏内容。"""
        if self.handle.isVisible():
            self.handle.move(self.x() + 2, self.y() + 2)

    def _on_handle_moved(self, position: QPoint) -> None:
        self.move(position.x() - 2, position.y() - 2)

    def _emit_bounds(self) -> None:
        geometry = self.geometry()
        self.bounds_changed.emit(
            {
                "x": geometry.x(),
                "y": geometry.y(),
                "width": geometry.width(),
                "height": geometry.height(),
            }
        )
