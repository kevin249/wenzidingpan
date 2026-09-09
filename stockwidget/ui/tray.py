"""系统托盘：组件常驻托盘，关掉窗口不等于退出。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QColorDialog, QMenu, QSystemTrayIcon

from ..config import Config
from .icon import tray_icon

MESSAGE_MSECS = 10_000
BASE_TOOLTIP = "股票行情组件"


def message_body(body: str) -> str:
    """气泡正文：折掉换行、截断到一行能看完的长度。

    正文为空时给个兜底——Windows 收到空正文的通知会直接不弹。
    """
    return " ".join(str(body or "").split())[:240] or "收到新的实时提醒"


class Tray(QSystemTrayIcon):
    normal_color_requested = Signal(str)
    alert_color_requested = Signal(str)

    def __init__(self, config: Config, parent=None) -> None:
        self._normal_color = config.tray_icon_normal_color
        self._alert_color = config.tray_icon_alert_color
        super().__init__(
            tray_icon(normal_color=self._normal_color, alert_color=self._alert_color), parent
        )
        self.setToolTip(BASE_TOOLTIP)
        self._unread = 0

        self._menu = QMenu()
        self.toggle_action = QAction("显示 / 隐藏", self._menu)
        self.refresh_action = QAction("立即刷新", self._menu)
        self.on_top_action = QAction("最前显示", self._menu, checkable=True)
        # 穿透开启后窗口点不到了，托盘是唯一能关掉它的地方，必须留在这里。
        self.click_through_action = QAction("鼠标穿透", self._menu, checkable=True)
        # 同理：按钮藏起来之后窗口上就没有开关它的入口了，托盘得留一个。
        self.title_buttons_action = QAction("显示标题栏按钮", self._menu, checkable=True)
        self.normal_color_action = QAction("常态图标颜色…", self._menu)
        self.alert_color_action = QAction("提醒图标颜色…", self._menu)
        self.settings_action = QAction("设置…", self._menu)
        self.quit_action = QAction("退出", self._menu)

        self.normal_color_action.triggered.connect(self._pick_normal_color)
        self.alert_color_action.triggered.connect(self._pick_alert_color)

        self._menu.addAction(self.toggle_action)
        self._menu.addAction(self.refresh_action)
        self._menu.addSeparator()
        self._menu.addAction(self.on_top_action)
        self._menu.addAction(self.click_through_action)
        self._menu.addAction(self.title_buttons_action)
        self._menu.addSeparator()
        self._menu.addAction(self.normal_color_action)
        self._menu.addAction(self.alert_color_action)
        self._menu.addAction(self.settings_action)
        self._menu.addSeparator()
        self._menu.addAction(self.quit_action)
        self.setContextMenu(self._menu)

        self.apply_config(config)

    def notify(self, title: str, body: str) -> bool:
        """弹一条系统通知。"""
        if not self.supportsMessages():
            return False
        self.showMessage(
            str(title or "").strip() or "MCP 提醒",
            message_body(body),
            QSystemTrayIcon.Information,
            MESSAGE_MSECS,
        )
        return True

    def _pick_normal_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._normal_color), None, "选择常态托盘图标颜色")
        if color.isValid():
            self.normal_color_requested.emit(color.name())

    def _pick_alert_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._alert_color), None, "选择提醒托盘图标颜色")
        if color.isValid():
            self.alert_color_requested.emit(color.name())

    def _refresh_icon(self) -> None:
        self.setIcon(
            tray_icon(
                alert=self._unread > 0,
                normal_color=self._normal_color,
                alert_color=self._alert_color,
            )
        )

    def set_unread(self, count: int) -> int:
        """更新未读数，并在常态色 / 提醒色之间切换。"""
        count = max(0, int(count))
        if count == self._unread:
            return count
        self._unread = count
        self._refresh_icon()
        self.setToolTip(
            f"{BASE_TOOLTIP}\n{min(count, 99)} 条未读提醒" if count else BASE_TOOLTIP
        )
        return count

    def apply_config(self, config: Config) -> None:
        # 颜色可在运行中修改；保留当前未读状态，只重新着色。
        colors_changed = (
            self._normal_color != config.tray_icon_normal_color
            or self._alert_color != config.tray_icon_alert_color
        )
        self._normal_color = config.tray_icon_normal_color
        self._alert_color = config.tray_icon_alert_color
        if colors_changed:
            self._refresh_icon()

        # 回填勾选状态时屏蔽信号，免得又反过来触发一次写配置。
        for action, checked in (
            (self.on_top_action, config.always_on_top),
            (self.click_through_action, config.click_through),
            (self.title_buttons_action, config.show_title_buttons),
        ):
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
