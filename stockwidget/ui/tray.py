"""系统托盘：组件常驻托盘，关掉窗口不等于退出。"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..config import Config
from .icon import tray_icon


class Tray(QSystemTrayIcon):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(tray_icon(), parent)
        self.setToolTip("股票行情组件")

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
