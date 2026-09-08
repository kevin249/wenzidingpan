"""系统托盘：组件常驻托盘，关掉窗口不等于退出。"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

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
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(tray_icon(), parent)
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
        self.settings_action = QAction("设置…", self._menu)
        self.quit_action = QAction("退出", self._menu)

        self._menu.addAction(self.toggle_action)
        self._menu.addAction(self.refresh_action)
        self._menu.addSeparator()
        self._menu.addAction(self.on_top_action)
        self._menu.addAction(self.click_through_action)
        self._menu.addAction(self.title_buttons_action)
        self._menu.addAction(self.settings_action)
        self._menu.addSeparator()
        self._menu.addAction(self.quit_action)
        self.setContextMenu(self._menu)

        self.apply_config(config)

    def notify(self, title: str, body: str) -> bool:
        """弹一条系统通知。

        Windows 上这是任务栏通知区的气泡 / Toast：终端没开、组件被别的窗口压住
        时它照样能冒出来，图标也会在通知区亮起。主窗口是 ``Qt.Tool``，没有任务栏
        按钮可闪，系统通知是这个组件唯一的系统级提示入口。
        """
        if not self.supportsMessages():
            return False
        self.showMessage(
            str(title or "").strip() or "MCP 提醒",
            message_body(body),
            QSystemTrayIcon.Information,
            MESSAGE_MSECS,
        )
        return True

    def set_unread(self, count: int) -> int:
        """把未读条数画到通知区图标上，返回实际生效的条数。

        通知区就在任务栏上，而且这块归组件自己管——不像终端铃铛那样要看用的是
        哪个终端、有没有开对设置、窗口是不是在前台。图标会一直挂着告警色直到
        用户去看，比闪一下、错过就没了更可靠。
        """
        count = max(0, int(count))
        if count == self._unread:
            return count
        self._unread = count
        self.setIcon(tray_icon(alert=count > 0))
        self.setToolTip(
            f"{BASE_TOOLTIP}\n{min(count, 99)} 条未读提醒" if count else BASE_TOOLTIP
        )
        return count

    def apply_config(self, config: Config) -> None:
        # 回填勾选状态时屏蔽信号，免得又反过来触发一次写配置。
        for action, checked in (
            (self.on_top_action, config.always_on_top),
            (self.click_through_action, config.click_through),
            (self.title_buttons_action, config.show_title_buttons),
        ):
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)
