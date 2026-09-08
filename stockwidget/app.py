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
        # 选平台插件必须赶在 QApplication 之前——它一建好就定了。
        desktop.apply_qt_platform()
        self.qt = QApplication(argv if argv is not None else sys.argv)
        self.qt.setApplicationName("stock-ticker-widget")
        # Linux 桌面靠这个把窗口和 .desktop 条目对上，否则任务栏/切换器里没有图标。
        self.qt.setDesktopFileName("stock-ticker-widget")
        self.qt.setQuitOnLastWindowClosed(False)  # 组件常驻托盘
        self.qt.setWindowIcon(tray_icon())
        # macOS 上降成附属应用：不占 Dock，也不出现在 ⌘-Tab 里。
        desktop.use_accessory_activation_policy()

        # store 可注入，便于冒烟脚本用临时配置跑，不污染用户真实配置。
        self.store = store or Store()
        # 手上留一份当前配置：提醒回调要按开关分发，不能每来一条就回 store 重新校验一遍。
        self.config = config = self.store.get()
        # 未读提醒数，画在通知区图标上；窗口 BELL 那边自己另记一份。
        self._unread = 0

        self.window = TickerWindow(config)
        self.window.restore_bounds(
            config.bounds, [screen.availableGeometry() for screen in self.qt.screens()]
        )
        self.window.refresh_requested.connect(self.refresh)
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.bounds_changed.connect(self._save_bounds)
        # 点掉窗口上的 BELL 也等于看过了，托盘图标要跟着一起消。
        self.window.bell_cleared.connect(self._clear_unread)
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
        # 点了托盘就算看过了：两处未读一起清，别让紧接着打开的窗口还挂着 BELL·N
        # ——同一个动作刚把它们标记成已读。
        self._clear_unread()
        self.window.clear_mcp_notifications()
        self.toggle_window()

    def _clear_unread(self) -> None:
        self._unread = 0
        if self.tray is not None:
            self.tray.set_unread(0)

    def _apply_config(self, config: Config) -> None:
        self.config = config
        # 总开关或这一路自己的开关只要关掉，就别把告警色留在通知区上——监听一停，
        # 之后再没有提醒能把它清掉，图标会一直红着。判据和窗口 BELL 那边保持一致。
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
            # 自动模式下把真正出数的源标出来，省得用户猜现在走的是哪家。
            label = f"自动 · {listing.get(snapshot.effective_provider, snapshot.effective_provider)}"
        self.window.update_snapshot(snapshot, label)

    def _on_mcp_status(self, status: str) -> None:
        if status != "已关闭":
            print(f"[MCP提醒] {status}", flush=True)

    def _on_mcp_notification(self, notification: McpNotification) -> None:
        timestamp = notification.created_at or "时间未知"
        print(f"[MCP提醒] {timestamp} | {notification.title}", flush=True)
        if notification.body:
            print(notification.body, flush=True)
        # 三路提示各有开关，全关就只剩上面这几行正文。
        # 正文本身只是普通输出，终端不会当成提示。得单独敲一下 BEL，
        # Windows Terminal 才会点亮标签铃铛、按 bellStyle 闪任务栏。
        if self.config.mcp_bell_terminal:
            desktop.ring_terminal_bell()
        if self.config.mcp_bell_toast and self.tray is not None:
            self.tray.notify(notification.title, notification.body)
        # 通知区图标转告警色：这一路归组件自己管，不看终端脸色，也不怕气泡被错过。
        if self.config.mcp_bell_tray_icon and self.tray is not None:
            self._unread += 1
            self.tray.set_unread(self._unread)
        if self.config.mcp_bell_window:
            self.window.show_mcp_notification(notification.title, notification.body)

    # ------------------------------------------------------------ 启动

    def startup_notes(self) -> list[str]:
        """当前平台上会影响使用的限制，启动时打一行提示。"""
        return desktop.startup_notes(
            platform_name=self.qt.platformName(),
            tray_available=self.tray is not None,
        )

    def run(self) -> int:
        # 让 Ctrl+C 能中断 Qt 事件循环
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
