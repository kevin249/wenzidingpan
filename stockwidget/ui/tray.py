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
        self.settings_action = QAction("设置…", self._menu)
        self.quit_action = QAction("退出", self._menu)

        self._menu.addAction(self.toggle_action)
        self._menu.addAction(self.refresh_action)
        self._menu.addSeparator()
        self._menu.addAction(self.on_top_action)
        self._menu.addAction(self.settings_action)
        self._menu.addSeparator()
        self._menu.addAction(self.quit_action)
        self.setContextMenu(self._menu)

        self.apply_config(config)

    def apply_config(self, config: Config) -> None:
        self.on_top_action.setChecked(config.always_on_top)
