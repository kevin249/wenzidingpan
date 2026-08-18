"""桌面悬浮窗：无边框、半透明、可最前显示、可拖拽。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ..config import Bounds, Config
from ..poller import Snapshot
from .marquee import Marquee
from .quote_row import QuoteRow
from .theme import BACKGROUND, BORDER, make_font

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None

        self.brand = QLabel("行情")
        self.status = QLabel("连接中…")
        self.brand.setStyleSheet("color: #8b93a7;")
        self.status.setStyleSheet("color: #8b93a7;")

        self.refresh_button = self._button("⟳", "立即刷新", self.refresh_requested)
        self.settings_button = self._button("⚙", "在浏览器中打开设置", self.settings_requested)
        self.quit_button = self._button("✕", "退出", self.quit_requested, QUIT_BUTTON_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 8, 5)
        layout.setSpacing(8)
        layout.addWidget(self.brand)
        layout.addWidget(self.status, 1)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.settings_button)
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
        for button in (self.refresh_button, self.settings_button, self.quit_button):
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


class TickerWindow(QWidget):
    """组件主窗口。"""

    refresh_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    bounds_changed = Signal(object)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._rows: dict[str, QuoteRow] = {}

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("股票行情组件")

        self.title_bar = TitleBar(self)
        self.title_bar.refresh_requested.connect(self.refresh_requested.emit)
        self.title_bar.settings_requested.connect(self.settings_requested.emit)
        self.title_bar.quit_requested.connect(self.quit_requested.emit)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 3, 0, 3)
        self.rows_layout.setSpacing(0)
        self.rows_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea, QWidget { background: transparent; }"
            "QScrollBar:vertical { width: 6px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.14); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
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
        footer_layout.addWidget(QSizeGrip(self.footer), 0, Qt.AlignBottom | Qt.AlignRight)

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

        self.apply_config(config)

    # ------------------------------------------------------------ 外观

    def apply_config(self, config: Config) -> None:
        previous = self._config
        self._config = config

        flags = Qt.FramelessWindowHint | Qt.Tool
        if config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if flags != self.windowFlags():
            visible = self.isVisible()
            self.setWindowFlags(flags)
            if visible:
                self.show()  # 改 flag 会隐藏窗口，需要重新显示

        self.setWindowOpacity(config.opacity)
        self.title_bar.apply_config(config)
        self.marquee.apply_config(config)
        self.updated_label.setFont(make_font(config, 0.78))
        self.empty_label.setFont(make_font(config, 0.9))
        for row in self._rows.values():
            row.apply_config(config)

        single = config.layout == "single"
        self.marquee.setVisible(single)
        self.scroll.setVisible(not single and bool(self._rows))
        self.empty_label.setVisible(not single and not self._rows)
        self.footer.setVisible(not single and not config.compact)

        if previous.color_scheme != config.color_scheme:
            self.update()
        self._resize_to_rows()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.fillPath(path, BACKGROUND)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(path)

    # ------------------------------------------------------------ 数据

    def update_snapshot(self, snapshot: Snapshot, provider_label: str) -> None:
        quotes = snapshot.quotes
        if self._config.layout == "single":
            self.marquee.set_quotes(quotes)
        else:
            self._sync_rows(quotes)

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

    def _sync_rows(self, quotes) -> None:
        for index, quote in enumerate(quotes):
            row = self._rows.get(quote.symbol)
            if row is None:
                row = QuoteRow(quote.symbol)
                row.apply_config(self._config)
                self._rows[quote.symbol] = row
            # 保持与配置一致的顺序
            self.rows_layout.insertWidget(index, row)
            row.update_quote(quote, self._config)

        wanted = {q.symbol for q in quotes}
        for symbol in list(self._rows):
            if symbol not in wanted:
                row = self._rows.pop(symbol)
                self.rows_layout.removeWidget(row)
                row.deleteLater()

        single = self._config.layout == "single"
        self.scroll.setVisible(not single and bool(self._rows))
        self.empty_label.setVisible(not single and not self._rows)
        self._resize_to_rows()

    # ------------------------------------------------------------ 尺寸

    def _resize_to_rows(self) -> None:
        """窗口高度跟着「行数 × 当前字号下的实际行高」走。"""
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
        row_height = next(iter(self._rows.values())).sizeHint().height()
        count = max(1, min(self._config.visible_rows, len(self._rows)))
        footer = self.footer.sizeHint().height() if self.footer.isVisible() else 0
        self.resize(self.width(), chrome + row_height * count + footer + 8)

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
        if visible:
            self.move(bounds.x, bounds.y)

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._save_timer.start()

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
