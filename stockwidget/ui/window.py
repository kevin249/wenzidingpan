"""桌面悬浮窗：无边框、半透明、可最前显示、可拖拽。"""

from __future__ import annotations

from dataclasses import replace
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
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

        self.refresh_button = self._button("⟳", "立即刷新", self.refresh_requested)
        self.settings_button = self._button("⚙", "在浏览器中打开设置", self.settings_requested)
        self.grayscale_button = self._button("灰", "切换彩色 / 灰度显示", self.grayscale_requested)
        self.quit_button = self._button("✕", "退出", self.quit_requested, QUIT_BUTTON_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(8)
        layout.addStretch(1)
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
        self.grayscale_button.setText("彩" if config.grayscale else "灰")
        for button in (self.refresh_button, self.settings_button, self.grayscale_button, self.quit_button):
            button.setFont(make_font(config, 0.95))
            button.setFixedSize(round(config.font_size * 1.7), round(config.font_size * 1.7))

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

    drag_started = Signal(QSize)
    dragged = Signal(QSize)  # 拖出来的新窗口尺寸
    drag_finished = Signal()

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
            self.drag_started.emit(self._start_size)
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
        self.drag_finished.emit()
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
        self._opacity = 1.0

    def set_opacity(self, opacity: float) -> None:
        """独立窗口不在主窗口的合成树里，需要单独同步透明度。"""
        self._opacity = opacity
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self._opacity)
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
        self._manual_size = False
        self._drag_start_size = QSize()
        self._drag_start_scale = 1.0
        self._drag_reference_height = 0
        self._restore_scale_from_height = False
        self._move_drag_offset: QPoint | None = None
        self._move_drag_origin: QPoint | None = None
        self._move_drag_active = False

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
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea QWidget { background: transparent; border: none; }"
        )
        # QScrollArea.setWidget() 会把内容控件的 autoFillBackground 自动打开，
        # 从系统调色板刷出一块不透明灰底。挂载后必须对三层表面全部清掉。
        for surface in (self.scroll, self.scroll.viewport(), self.rows_host):
            surface.setAutoFillBackground(False)
            palette = surface.palette()
            palette.setColor(QPalette.Window, QColor(0, 0, 0, 0))
            palette.setColor(QPalette.Base, QColor(0, 0, 0, 0))
            surface.setPalette(palette)

        self.marquee = Marquee()
        self.empty_label = QLabel("自选列表为空\n点击 ⚙ 在浏览器里添加代码")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #8b93a7;")

        self.footer = QWidget()
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(12, 2, 4, 2)
        footer_layout.addStretch(1)
        self.grip = ResizeGrip(self.footer)
        self.grip.drag_started.connect(self._on_grip_drag_started)
        self.grip.dragged.connect(self._on_grip_dragged)
        self.grip.drag_finished.connect(self._on_grip_drag_finished)
        footer_layout.addWidget(self.grip, 0, Qt.AlignBottom | Qt.AlignRight)

        layout = QVBoxLayout(self)
        # 顶层布局不能用默认的 sizeConstraint 自动撑大窗口，否则启动恢复保存高度后，
        # 首批行情控件加入布局时会再次改写外框尺寸。
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.marquee)
        layout.addWidget(self.footer)

        # 顶层透明特效在 Windows 上可能漏掉使用系统调色板的 QLabel。
        # 按主要内容层分别合成，既完整覆盖子控件，又不会重复叠加透明度。
        self._opacity_effects: list[QGraphicsOpacityEffect] = []
        for surface in (self.title_bar, self.empty_label, self.rows_host, self.marquee, self.footer):
            effect = QGraphicsOpacityEffect(surface)
            surface.setGraphicsEffect(effect)
            self._opacity_effects.append(effect)

        # 未开启鼠标穿透时，窗口里的行情文字、走势图、空白区和按钮都可按住拖动。
        # 滚动条及右下角缩放柄保留各自原本的交互。
        self._install_move_filters(self)

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
        """所有独立字号统一乘窗口缩放系数，保持用户设置的相对比例。"""
        if abs(self._scale - 1.0) < 0.01:
            return self._config

        def scaled(value: int, low: int = 7, high: int = 96) -> int:
            return int(round(_clamp(value * self._scale, low, high)))

        return replace(
            self._config,
            font_size=scaled(self._config.font_size, 8, 48),
            stock_name_font_size=scaled(self._config.stock_name_font_size),
            stock_price_font_size=scaled(self._config.stock_price_font_size),
            stock_percent_font_size=scaled(self._config.stock_percent_font_size),
            dark_trade_font_size=scaled(self._config.dark_trade_font_size),
            chart_label_font_size=scaled(self._config.chart_label_font_size),
            # 0 表示走势图高度自动，不参与缩放；设了固定高度才跟着窗口一起放大。
            chart_height=(
                scaled(self._config.chart_height, 8, 400) if self._config.chart_height else 0
            ),
        )

    def apply_config(self, config: Config) -> None:
        previous = self._config
        self._config = config

        flags = Qt.FramelessWindowHint | Qt.Tool
        if config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if config.click_through:
            # 让整窗不吃鼠标事件，点击直接落到下面的程序上。
            flags |= Qt.WindowTransparentForInput
            self._cancel_window_drag()
        if flags != self.windowFlags():
            visible = self.isVisible()
            self.setWindowFlags(flags)
            if visible:
                self.show()  # 改 flag 会隐藏窗口，需要重新显示

        scaled = self.scaled_config()
        self.setWindowOpacity(1.0)
        for effect in self._opacity_effects:
            effect.setOpacity(config.opacity)
        self.handle.set_opacity(config.opacity)
        self.title_bar.apply_config(scaled)
        self.marquee.apply_config(scaled)
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

        if (
            previous.color_scheme,
            previous.opacity,
            previous.background_color,
            previous.background_alpha,
        ) != (
            config.color_scheme,
            config.opacity,
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
        background.setAlphaF(self._config.background_alpha * self._config.opacity)
        painter.fillPath(path, background)
        if self._config.background_alpha > 0.02:  # 全透明时连边框也不该留
            border = QColor(BORDER)
            border.setAlphaF(BORDER.alphaF() * self._config.opacity)
            painter.setPen(QPen(border, 1))
            painter.drawPath(path)

    # ------------------------------------------------------------ 数据

    def update_snapshot(self, snapshot: Snapshot, _provider_label: str) -> None:
        quotes = snapshot.quotes
        if self._config.layout == "single":
            self.marquee.set_quotes(quotes)
        else:
            self._sync_rows(quotes, snapshot.trends)

    def _sync_rows(self, quotes, trends: dict | None = None) -> None:
        trends = trends or {}
        scaled = self.scaled_config()
        for index, quote in enumerate(quotes):
            row = self._rows.get(quote.symbol)
            if row is None:
                row = QuoteRow(quote.symbol)
                row.apply_config(scaled)
                self._install_move_filters(row)
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
        self._sync_restored_scale_from_height()

    def _grid_size(self, count: int) -> tuple[int, int]:
        """设置里的行数决定网格有几行，列数由自选数量摊出来。"""
        rows = max(1, min(self._config.visible_rows, max(count, 1)))
        columns = max(1, -(-count // rows))  # 向上取整
        return rows, columns

    def _lay_out_grid(self, order: list[str]) -> None:
        """按自选顺序从左到右填，填满一行再换下一行。"""
        rows, columns = self._grid_size(len(order))
        # 禁止滚动条：内容宿主始终跟随视口宽度，每个 QuoteRow 再按实际格宽
        # 在横向版与窄卡片版之间自适应。
        self.rows_host.setMinimumWidth(0)
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
            if not self._manual_size:
                self.resize(self.width(), chrome + 90)
                self._keep_on_screen()
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
            columns * self._column_floor() + 2,
        )
        screen = self.screen()
        if screen is not None:  # 列太多时别撑出屏幕，剩下的交给横向滚动
            width = min(width, screen.availableGeometry().width())
        if not self._manual_size:
            self.resize(width, height)
        self._keep_on_screen()

    def _column_floor(self) -> int:
        """自动排版时每列至少要多宽。

        选了左中右就得留够三列的宽度，否则格子一窄，QuoteRow 会自己退回上中下，
        而窗口宽度又是照着退回后的 sizeHint 算的——两边互相迁就就再也回不去了。
        """
        scaled = self.scaled_config()
        if self._config.row_style != "sides":
            return round(scaled.font_size * 13)
        side_font = max(scaled.stock_name_font_size, scaled.stock_price_font_size)
        # 与 QuoteRow 判定窄卡片的阈值保持一致，再多给几像素避免边界抖动。
        return max(120, round(side_font * 12)) + 4

    def restore_bounds(self, bounds: Bounds | None, available: list) -> None:
        """恢复上次位置前先确认它仍落在某块屏幕上（外接显示器可能已拔掉）。"""
        if bounds is None:
            self.resize(300, 260)
            return
        self._scale = _clamp(bounds.scale, 0.6, 3.0)
        self._manual_size = bounds.manual_size
        self._restore_scale_from_height = (
            bounds.manual_size and bounds.height >= 160 and bounds.scale < 0.9
        )
        if not available:
            self.resize(bounds.width, bounds.height)
            self.move(bounds.x, bounds.y)
            return

        saved = QRect(bounds.x, bounds.y, bounds.width, bounds.height)
        target = max(available, key=lambda geo: _intersection_area(saved, geo))
        if _intersection_area(saved, target) == 0:
            target = available[0]
        width = min(bounds.width, target.width())
        height = min(bounds.height, target.height())
        x = round(_clamp(bounds.x, target.left(), target.right() - width + 1))
        y = round(_clamp(bounds.y, target.top(), target.bottom() - height + 1))
        self.setGeometry(x, y, width, height)
        # restore_bounds 在构造后的 apply_config 之后调用，恢复比例后需立即刷新字体。
        self._apply_scale()

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()
        self._move_handle()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()
        self._move_handle()

    # ------------------------------------------------------------ 整窗拖动

    def _install_move_filters(self, widget: QWidget) -> None:
        """监听整棵控件树，让行情内容区也能发起窗口移动。"""
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        event_type = event.type()
        mouse_event = event_type in (
            QEvent.MouseButtonPress,
            QEvent.MouseMove,
            QEvent.MouseButtonRelease,
        )
        if not mouse_event:
            return super().eventFilter(watched, event)

        # 标题栏已有等价的原生处理；滚动条和缩放柄不能被移动手势抢占。
        if watched is self.title_bar or isinstance(watched, (QAbstractSlider, ResizeGrip)):
            return super().eventFilter(watched, event)

        if self._config.click_through:
            self._cancel_window_drag()
            return False

        if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            position = event.globalPosition().toPoint()
            self._move_drag_origin = position
            self._move_drag_offset = position - self.frameGeometry().topLeft()
            self._move_drag_active = False
            return False  # 未达到拖动阈值时，按钮仍可正常单击。

        if (
            event_type == QEvent.MouseMove
            and self._move_drag_origin is not None
            and self._move_drag_offset is not None
            and event.buttons() & Qt.LeftButton
        ):
            position = event.globalPosition().toPoint()
            if not self._move_drag_active:
                distance = (position - self._move_drag_origin).manhattanLength()
                if distance < QApplication.startDragDistance():
                    return False
                self._move_drag_active = True
                if isinstance(watched, QAbstractButton):
                    watched.setDown(False)
            self.move(position - self._move_drag_offset)
            event.accept()
            return True

        if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            was_dragging = self._move_drag_active
            if was_dragging and isinstance(watched, QAbstractButton):
                watched.setDown(False)
            self._cancel_window_drag()
            if was_dragging:
                event.accept()
                return True

        return False

    def _cancel_window_drag(self) -> None:
        self._move_drag_offset = None
        self._move_drag_origin = None
        self._move_drag_active = False

    def _on_grip_drag_started(self, size: QSize) -> None:
        """记录本次手势，并以当前列宽下的基准高度校准内容缩放。"""
        self._drag_start_size = QSize(size)
        self._drag_start_scale = self._scale
        self._drag_reference_height = self._base_frame_height(size.width())
        self._manual_size = True

    def _on_grip_dragged(self, size: QSize) -> None:
        """按外框的限制轴缩放内容，并阻止右、下边缘越出屏幕。"""
        if not self._drag_start_size.isValid():
            self._on_grip_drag_started(self.size())
        size = self._bounded_drag_size(size)
        self.resize(size)
        start_height = max(1, self._drag_start_size.height())
        # 字体由高度决定；宽度只负责给走势图更多或更少的横向空间。
        # 有行情行时使用绝对基准，避免历史上的错误 scale 一直累积。
        if self._drag_reference_height > 0:
            scale = _clamp(size.height() / self._drag_reference_height, 0.6, 3.0)
        else:
            scale = _clamp(
                self._drag_start_scale * size.height() / start_height,
                0.6,
                3.0,
            )
        if abs(scale - self._scale) > 0.02:
            self._scale = scale
            self._scale_timer.start()

    def _on_grip_drag_finished(self) -> None:
        self._drag_start_size = QSize()
        self._drag_reference_height = 0

    def _base_frame_height(self, frame_width: int) -> int:
        """计算当前列宽下、缩放为 1 时窗口内容所需的自然高度。"""
        if not self._rows:
            return 0

        base = self._config
        title_probe = TitleBar()
        title_probe.apply_config(base)
        chrome = title_probe.sizeHint().height() + 2

        if base.layout == "single":
            marquee_probe = Marquee()
            marquee_probe.apply_config(base)
            return chrome + marquee_probe.height() + 4

        rows, columns = self._grid_size(len(self._rows))
        cell_width = max(1, (frame_width - 2) // columns)
        row_probe = QuoteRow("__scale_probe__")
        row_probe.name_label.setText("示例股票")
        row_probe.price_label.setText("9999.99")
        row_probe.percent_label.setText("+99.99%")
        row_probe.dark_value.setText("+99.99亿")
        row_probe.resize(cell_width, 100)
        row_probe.apply_config(base)

        footer = self.footer.sizeHint().height() if not base.compact else 0
        return chrome + row_probe.sizeHint().height() * rows + footer + 8

    def _sync_restored_scale_from_height(self) -> None:
        """旧配置可能保存了错误比例；首批行情出现后按实际窗口高度修正。"""
        if not self._restore_scale_from_height or not self._rows:
            return
        self._restore_scale_from_height = False
        reference_height = self._base_frame_height(self.width())
        if reference_height <= 0:
            return
        self._scale = _clamp(self.height() / reference_height, 0.6, 3.0)
        self._apply_scale()

    def _bounded_drag_size(self, size: QSize) -> QSize:
        screen = self.screen()
        if screen is None:
            return QSize(max(180, size.width()), max(80, size.height()))
        geo = screen.availableGeometry()
        max_width = max(1, geo.right() - max(self.x(), geo.left()) + 1)
        max_height = max(1, geo.bottom() - max(self.y(), geo.top()) + 1)
        min_width = min(180, max_width)
        min_height = min(80, max_height)
        return QSize(
            round(_clamp(size.width(), min_width, max_width)),
            round(_clamp(size.height(), min_height, max_height)),
        )

    def _keep_on_screen(self) -> None:
        """自动布局可能改变窗口尺寸；完成后保证整个窗口仍在当前屏幕内。"""
        screen = self.screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = min(self.width(), geo.width())
        height = min(self.height(), geo.height())
        x = round(_clamp(self.x(), geo.left(), geo.right() - width + 1))
        y = round(_clamp(self.y(), geo.top(), geo.bottom() - height + 1))
        if (x, y, width, height) != (self.x(), self.y(), self.width(), self.height()):
            self.setGeometry(x, y, width, height)

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
                "scale": round(self._scale, 3),
                "manual_size": self._manual_size,
            }
        )

    def flush_bounds(self) -> None:
        """退出前立即保存最后一次位置与比例，不等待防抖定时器。"""
        self._save_timer.stop()
        self._emit_bounds()


def _intersection_area(first: QRect, second: QRect) -> int:
    intersection = first.intersected(second)
    return max(0, intersection.width()) * max(0, intersection.height())
