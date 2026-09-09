"""把配置、轮询线程、桌面窗口、托盘和 WebUI 接到一起。"""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QObject, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import desktop, providers
from .config import Config, Store
from .mcp_notifications import McpNotification, McpNotificationListener
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
        desktop.apply_qt_platform()
        self.qt = QApplication(argv if argv is not None else sys.argv)
        self.qt.setApplicationName("stock-ticker-widget")
        self.qt.setDesktopFileName("stock-ticker-widget")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(tray_icon())
        desktop.use_accessory_activation_policy()

        self.store = store or Store()
        self.config = config = self.store.get()
        self._unread = 0

        self.window = TickerWindow(config)
        self.window.restore_bounds(
            config.bounds, [screen.availableGeometry() for screen in self.qt.screens()]
        )
        self.window.refresh_requested.connect(self.refresh)
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.bounds_changed.connect(self._save_bounds)
        self.window.bell_cleared.connect(self._clear_unread)
        self.qt.aboutToQuit.connect(self._flush_bounds)
        self.window.grayscale_requested.connect(
            lambda: self._apply_config(self.store.update({"grayscale": not self.store.get().grayscale}))
        )
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
        self.notification_listener = McpNotificationListener(config)
        self.notification_listener.notification_ready.connect(
            self._on_mcp_notification, Qt.QueuedConnection
        )
        self.notification_listener.status_changed.connect(self._on_mcp_status, Qt.QueuedConnection)

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
            self.tray.activated.connect(self._on_tray_activated)

    # ------------------------------------------------------------ 动作

    def refresh(self) -> None:
        self.poller.refresh_now()

    def open_settings(self) -> None:
        QDesktopServices.openUrl(QUrl(self.server.url))

    def toggle_window(self) -> None:
        self.window.hide() if self.window.isVisible() else self.window.show()

    def _show_window(self) -> None:
        """确保窗口可见并提到前面；用于用户点击未读提醒的托盘图标。"""
        if not self.window.isVisible():
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        self._flush_bounds()
        self.poller.stop()
        self.poller.wait(2000)
        self.notification_listener.stop()
        self.notification_listener.wait(9000)
        self.server.stop()
        self.qt.quit()

    # ------------------------------------------------------------ 回调

    def _on_tray_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.Trigger:
            return

        had_unread = self._unread > 0
        if had_unread:
            # 有未读时，左键的首要语义是“我看到了”：清掉两处未读，并把窗口显示出来。
            # 不能再顺手 toggle，否则窗口本来可见时第一次点击反而把它藏掉，用户还得
            # 再点第二次才能看到行情。
            self._clear_unread()
            self.window.clear_mcp_notifications()
            self._show_window()
            return

        # 没有未读时，保留原来的显示 / 隐藏快捷操作。
        self.toggle_window()

    def _clear_unread(self) -> None:
        self._unread = 0
        if self.tray is not None:
            self.tray.set_unread(0)

    def _apply_config(self, config: Config) -> None:
        self.config = config
        if not (config.mcp_notifications_enabled and config.mcp_bell_tray_icon):
            self._clear_unread()
        self.window.apply_config(config)
        self.poller.apply_config(config)
        self.notification_listener.apply_config(config)
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
            label = f"自动 · {listing.get(snapshot.effective_provider, snapshot.effective_provider)}"
        self.window.update_snapshot(snapshot, label)

    def _on_mcp_status(self, status: str) -> None:
        if status != "已关闭":
            print(f"[MCP提醒] {status}", flush=True)

    def _on_mcp_notification(self, notification: McpNotification) -> None:
        timestamp = notification.created_at or "时间未知"
        print("\n======\n", flush=True)
        print(f"[MCP提醒] {timestamp} | {notification.title}", flush=True)
        if notification.body:
            print(notification.body, flush=True)
        if self.config.mcp_bell_terminal:
            desktop.ring_terminal_bell()
        if self.config.mcp_bell_toast and self.tray is not None:
            self.tray.notify(notification.title, notification.body)
        if self.config.mcp_bell_tray_icon and self.tray is not None:
            self._unread += 1
            self.tray.set_unread(self._unread)
        if self.config.mcp_bell_window:
            self.window.show_mcp_notification(notification.title, notification.body)

    # ------------------------------------------------------------ 启动

    def startup_notes(self) -> list[str]:
        return desktop.startup_notes(
            platform_name=self.qt.platformName(),
            tray_available=self.tray is not None,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, lambda *_: self.quit())

        url = self.server.start()
        print(f"设置页：{url}")
        for note in self.startup_notes():
            print(f"[提示] {note}")
        self.window.show()
        if self.tray is not None:
            self.tray.show()
        self.poller.start()
        self.notification_listener.start()
        return self.qt.exec()


def main() -> int:
    return WidgetApp().run()
