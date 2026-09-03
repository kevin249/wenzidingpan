"""把配置、轮询线程、桌面窗口、托盘和 WebUI 接到一起。"""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import providers
from .config import Config, Store
from .poller import Poller, Snapshot
from .ui.icon import tray_icon
from .ui.tray import Tray
from .ui.window import TickerWindow
from .webui import SettingsServer


class ConfigBridge(QObject):
    """WebUI 跑在另一个线程，配置变更通过信号回到界面线程。"""

    changed = Signal(object)


class WidgetApp:
    def __init__(self, argv: list[str] | None = None, store: Store | None = None) -> None:
        self.qt = QApplication(argv if argv is not None else sys.argv)
        self.qt.setApplicationName("stock-ticker-widget")
        self.qt.setQuitOnLastWindowClosed(False)  # 组件常驻托盘
        self.qt.setWindowIcon(tray_icon())

        # store 可注入，便于冒烟脚本用临时配置跑，不污染用户真实配置。
        self.store = store or Store()
        config = self.store.get()

        self.window = TickerWindow(config)
        self.window.restore_bounds(
            config.bounds, [screen.availableGeometry() for screen in self.qt.screens()]
        )
        self.window.refresh_requested.connect(self.refresh)
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.bounds_changed.connect(self._save_bounds)
        self.qt.aboutToQuit.connect(self._flush_bounds)
        self.window.grayscale_requested.connect(
            lambda: self._apply_config(self.store.update({"grayscale": not self.store.get().grayscale}))
        )
        # 右键菜单里的开关，没有系统托盘时就靠它把标题栏按钮找回来。
        self.window.title_buttons_requested.connect(
            lambda: self._apply_config(
                self.store.update(
                    {"show_title_buttons": not self.store.get().show_title_buttons}
                )
            )
        )

        self.bridge = ConfigBridge()
        self.bridge.changed.connect(self._apply_config, Qt.QueuedConnection)
        self.server = SettingsServer(self.store, on_change=self.bridge.changed.emit)

        self.poller = Poller(config)
        self.poller.snapshot_ready.connect(self._on_snapshot, Qt.QueuedConnection)

        self.tray: Tray | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = Tray(config)
            self.tray.toggle_action.triggered.connect(self.toggle_window)
            self.tray.refresh_action.triggered.connect(self.refresh)
            self.tray.settings_action.triggered.connect(self.open_settings)
            self.tray.quit_action.triggered.connect(self.quit)
            self.tray.on_top_action.toggled.connect(
                lambda checked: self._apply_config(self.store.update({"always_on_top": checked}))
            )
            self.tray.click_through_action.toggled.connect(
                lambda checked: self._apply_config(self.store.update({"click_through": checked}))
            )
            self.tray.title_buttons_action.toggled.connect(
                lambda checked: self._apply_config(
                    self.store.update({"show_title_buttons": checked})
                )
            )
            self.tray.activated.connect(
                lambda reason: self.toggle_window()
                if reason == QSystemTrayIcon.Trigger
                else None
            )

    # ------------------------------------------------------------ 动作

    def refresh(self) -> None:
        self.poller.refresh_now()

    def open_settings(self) -> None:
        QDesktopServices.openUrl(QUrl(self.server.url))

    def toggle_window(self) -> None:
        self.window.hide() if self.window.isVisible() else self.window.show()

    def quit(self) -> None:
        self._flush_bounds()
        self.poller.stop()
        self.poller.wait(2000)
        self.server.stop()
        self.qt.quit()

    # ------------------------------------------------------------ 回调

    def _apply_config(self, config: Config) -> None:
        self.window.apply_config(config)
        self.poller.apply_config(config)
        if self.tray is not None:
            self.tray.apply_config(config)

    def _save_bounds(self, bounds: dict) -> None:
        self.store.update({"bounds": bounds})

    def _flush_bounds(self) -> None:
        self.window.flush_bounds()

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        listing = {p["id"]: p["label"] for p in providers.listing()}
        label = listing.get(snapshot.provider_id, snapshot.provider_id)
        if snapshot.effective_provider:
            # 自动模式下把真正出数的源标出来，省得用户猜现在走的是哪家。
            label = f"自动 · {listing.get(snapshot.effective_provider, snapshot.effective_provider)}"
        self.window.update_snapshot(snapshot, label)

    # ------------------------------------------------------------ 启动

    def run(self) -> int:
        # 让 Ctrl+C 能中断 Qt 事件循环
        signal.signal(signal.SIGINT, lambda *_: self.quit())

        url = self.server.start()
        print(f"设置页：{url}")
        self.window.show()
        if self.tray is not None:
            self.tray.show()
        self.poller.start()
        return self.qt.exec()


def main() -> int:
    return WidgetApp().run()
